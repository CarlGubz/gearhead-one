# Pinned to bookworm: the unpinned tag now resolves to Debian trixie, which no longer ships calculix-ccx
FROM python:3.12-slim-bookworm

WORKDIR /app

# Shared libraries for Gmsh (FEA meshing / CAD import) and OpenCascade (cadquery, the CAD kernel), plus CalculiX
# for the CAD workspace's nonlinear / buckling / transient / contact analyses (optional: it is found on PATH).
RUN apt-get update && apt-get install -y --no-install-recommends \
        calculix-ccx \
        libgl1 libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1 libxext6 libsm6 libfontconfig1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional Docker CLI so the CFD backends can start OpenFOAM / FEniCSx / PyFR containers (docker-compose.solvers.yml)
ARG WITH_DOCKER_CLI=0
RUN if [ "$WITH_DOCKER_CLI" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends docker.io     && rm -rf /var/lib/apt/lists/*; fi

COPY app ./app

# /app/data holds CFD run results (data/cfd) and uploaded CAD STEP files (data/cad) - mount a volume there
RUN useradd --create-home --uid 10001 app && mkdir -p /app/data/cfd /app/data/cad && chown -R app /app
USER app

# CCX_THREADS=1: Debian's multithreaded SPOOLES build returns spurious buckling eigenvalues (see app/cad/services/calculix.py)
ENV CFD_DATA_DIR=/app/data/cfd CAD_DATA_DIR=/app/data/cad CCX_THREADS=1 PYTHONUNBUFFERED=1

EXPOSE 8000
# One worker process: CFD runs are scheduled by an in-process thread (each run is its own solver process), CAD
# simulations run as background tasks and Gmsh keeps global state.
# --proxy-headers: trust X-Forwarded-* from nginx. The backend port is NOT published to the host.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
