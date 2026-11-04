"""Running external solvers: natively, in a Docker image or inside WSL.

Configuration (environment variables, see README "Configuration"):

* ``CFD_<TOOL>_LAUNCHER`` = ``auto`` | ``native`` | ``docker`` | ``wsl`` | ``off``
* ``CFD_<TOOL>_DOCKER_IMAGE`` = image used by the docker launcher
* ``CFD_<TOOL>_WSL_DISTRO`` = WSL distribution (empty = default)

``auto`` picks the first that works: native executable on ``PATH`` -> Docker image present
locally -> unavailable (the case then runs on the Python fallback if allowed).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .base import Availability

_CACHE: dict[tuple, tuple[float, Availability]] = {}
CACHE_SECONDS = 30.0


@dataclass
class Launcher:
    tool: str  # openfoam | fenicsx | pyfr
    mode: str  # native | docker | wsl
    image: str = ""
    distro: str = ""

    def command(self, case_dir: Path, argv: list[str], name: str) -> list[str]:
        """Command line that runs ``argv`` with ``case_dir`` as working directory."""
        case_dir = Path(case_dir).resolve()
        if self.mode == "docker":
            cmd = ["docker", "run", "--rm", "--name", name, "-v", f"{host_path(case_dir)}:/case", "-w", "/case"]
            if hasattr(os, "getuid") and os.environ.get("CFD_DOCKER_AS_ROOT", "0") != "1":
                cmd += ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]
            # some images' entrypoints (e.g. OpenFOAM's /openfoam/run) change directory: cd explicitly
            return cmd + [self.image, "bash", "-c", 'cd /case && exec "$@"', "cfdflow"] + argv
        if self.mode == "wsl":
            inner = " ".join(_sh_quote(a) for a in argv)
            base = ["wsl"] + (["-d", self.distro] if self.distro else [])
            return base + ["--cd", str(case_dir), "-e", "bash", "-lc", inner]
        return argv

    def kill(self, proc: subprocess.Popen, name: str) -> None:
        if self.mode == "docker":
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=30)
        if proc.poll() is None:
            proc.kill()


def host_path(p: Path) -> str:
    """Path as seen by the Docker daemon. When CFDFlow itself runs in a container that talks to the
    host daemon (socket mount), ``CFD_DOCKER_PATH_MAP="/data=/srv/cfdflow-data"`` translates the
    container path prefix of the data directory into the host path."""
    s = str(p)
    mapping = os.environ.get("CFD_DOCKER_PATH_MAP", "")
    for pair in filter(None, mapping.split(";")):
        inner, _, outer = pair.partition("=")
        if inner and outer and s.startswith(inner):
            return outer + s[len(inner):]
    return s


def _sh_quote(s: str) -> str:
    if s and all(ch.isalnum() or ch in "-_./=:" for ch in s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _docker_image_present(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _wsl_has(executable: str, distro: str) -> bool:
    if sys.platform != "win32" or not shutil.which("wsl"):
        return False
    try:
        base = ["wsl"] + (["-d", distro] if distro else [])
        r = subprocess.run(base + ["-e", "bash", "-lc", f"command -v {executable}"], capture_output=True, timeout=20)
        return r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def resolve_launcher(tool: str, native_probe: Callable[[], bool], native_exe: str, default_image: str,
                     setting: str, image: str | None, distro: str = "") -> tuple[Launcher | None, Availability]:
    """Find a way to run ``tool``. Results are cached for a short time (probing docker is slow)."""
    image = image or default_image
    key = (tool, setting, image, distro)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        av = hit[1]
        return (Launcher(tool, av.mode, image, distro) if av.ok else None), av
    order = ["native", "docker", "wsl"] if setting == "auto" else [setting]
    av = Availability(False, "", f"{tool} launcher is disabled" if setting == "off" else "")
    tried = []
    for mode in order:
        if mode == "off":
            break
        if mode == "native" and native_probe():
            av = Availability(True, "native", f"{native_exe} found on PATH")
            break
        if mode == "docker" and _docker_image_present(image):
            av = Availability(True, "docker", f"Docker image {image}")
            break
        if mode == "wsl" and _wsl_has(native_exe, distro):
            av = Availability(True, "wsl", f"WSL{' ' + distro if distro else ''}: {native_exe}")
            break
        tried.append({"native": f"no {native_exe} on PATH", "docker": f"Docker image {image} not present",
                      "wsl": "WSL not available or tool missing"}[mode])
    if not av.ok and not av.detail:
        av.detail = "; ".join(tried)
    _CACHE[key] = (time.time(), av)
    return (Launcher(tool, av.mode, image, distro) if av.ok else None), av


def clear_cache() -> None:
    _CACHE.clear()


def run_streaming(cmd: list[str], cwd: Path, log_path: Path, *, on_lines: Callable[[list[str]], None],
                  should_stop: Callable[[], bool], on_stop: Callable[[], None] | None = None,
                  kill: Callable[[subprocess.Popen], None] | None = None, grace: float = 20.0,
                  poll: float = 0.5, env: dict | None = None) -> tuple[int, bool]:
    """Run ``cmd`` writing stdout+stderr to ``log_path``; feed new lines to ``on_lines``.

    When ``should_stop()`` turns true, ``on_stop`` is called once (graceful stop, e.g. OpenFOAM
    ``stopAt writeNow``); after ``grace`` seconds the process is killed. Returns (exit code, stopped).
    """
    cwd = Path(cwd)
    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=logf, stderr=subprocess.STDOUT, env=env)
    pos = 0
    buf = b""
    stopped = False
    stop_at = None
    lock = threading.Lock()

    def read_new():
        nonlocal pos, buf
        try:
            with open(log_path, "rb") as f:
                f.seek(pos)
                chunk = f.read()
                pos += len(chunk)
        except OSError:
            return
        if not chunk:
            return
        buf += chunk
        *lines, buf = buf.split(b"\n")
        if lines:
            with lock:
                on_lines([ln.decode("utf-8", "replace").rstrip("\r") for ln in lines])

    while proc.poll() is None:
        time.sleep(poll)
        read_new()
        if not stopped and should_stop():
            stopped = True
            stop_at = time.time()
            if on_stop:
                on_stop()
            else:
                (kill or (lambda p: p.kill()))(proc)
        if stopped and stop_at and time.time() - stop_at > grace and proc.poll() is None:
            (kill or (lambda p: p.kill()))(proc)
    read_new()
    if buf:
        on_lines([buf.decode("utf-8", "replace")])
    return proc.returncode, stopped
