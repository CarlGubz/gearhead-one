"""
Parts (bodies) and the material library, as stored on a project.

parts      [{id, name, elem_start, elem_end, material_id}]   contiguous element ranges tiling the mesh
materials  [{id, name, E, nu, ...}]                          extra library entries; "default" is project.material
surface_parts[i] = index (into `parts`) of the part that owns render triangle i

Everything here works on plain data / the Project ORM row; the solver reads the same fields via jobs.project_to_dict.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .fea.model import FEMesh

DEFAULT_MATERIAL = "default"


def default_parts(n_elems: int, name: str = "Body", material_id: str = DEFAULT_MATERIAL) -> List[Dict[str, Any]]:
    return [{"id": "p0", "name": name, "elem_start": 0, "elem_end": int(n_elems), "material_id": material_id}]


def effective_parts(parts: Optional[list], n_elems: int) -> List[Dict[str, Any]]:
    """Persisted parts, or the single implicit part of a project created before parts existed."""
    if parts:
        return [dict(p) for p in parts]
    return default_parts(n_elems) if n_elems else []


def new_id(existing: List[Dict[str, Any]], prefix: str) -> str:
    ids = {str(x.get("id")) for x in existing}
    k = len(ids)
    while f"{prefix}{k}" in ids:
        k += 1
    return f"{prefix}{k}"


def surface_parts(mesh: FEMesh, parts: List[Dict[str, Any]]) -> List[int]:
    """Part index for every render triangle (same order as mesh.boundary().tris)."""
    b = mesh.boundary()
    starts = np.array([p["elem_start"] for p in parts])
    elem_part = np.searchsorted(starts, b.elem[b.tri_face], side="right") - 1
    return elem_part.astype(int).tolist()


def part_nodes(elems: np.ndarray, part: Dict[str, Any]) -> np.ndarray:
    return np.unique(elems[part["elem_start"]:part["elem_end"]])


def check_tiling(parts: List[Dict[str, Any]], n_elems: int) -> None:
    edge = 0
    for p in sorted(parts, key=lambda q: q["elem_start"]):
        if p["elem_start"] != edge or p["elem_end"] <= p["elem_start"]:
            raise ValueError("Part definitions must tile the mesh without gaps or overlaps")
        edge = p["elem_end"]
    if edge != n_elems:
        raise ValueError("Part definitions do not cover every element")


# --------------------------------------------------------------------- move
def translate(nodes: np.ndarray, elems: np.ndarray, parts: List[Dict[str, Any]], pid: str, offset) -> np.ndarray:
    """Return a copy of `nodes` with the part's nodes shifted. Refuses if its nodes are shared with another part."""
    part = next((p for p in parts if p["id"] == pid), None)
    if part is None:
        raise KeyError(pid)
    mine = part_nodes(elems, part)
    others = np.unique(np.concatenate([part_nodes(elems, p) for p in parts if p["id"] != pid] or [np.array([], dtype=int)]))
    if np.intersect1d(mine, others).size:
        raise ValueError("This part shares nodes with another part (a conforming mesh), so it cannot be moved on its own.")
    out = nodes.copy()
    out[mine] += np.asarray(offset, dtype=float)
    return out


# ------------------------------------------------------------------- delete
def _map_ids(ids, remap: np.ndarray) -> Tuple[List[int], bool]:
    kept = [int(remap[i]) for i in ids if 0 <= i < len(remap) and remap[i] >= 0]
    return kept, len(kept) != len(ids)


def _map_faces(faces, remap: np.ndarray) -> List[List[int]]:
    out = []
    for t in faces or []:
        if all(0 <= n < len(remap) and remap[n] >= 0 for n in t):
            out.append([int(remap[n]) for n in t])
    return out


def remove_part(nodes: np.ndarray, elems: np.ndarray, parts: List[Dict[str, Any]], pid: str) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]], np.ndarray, int]:
    """
    Drop a part: returns (nodes, elems, parts, node_remap, n_removed_elements).
    `node_remap[old] = new index` or -1 for removed nodes. Nodes shared with surviving parts are kept.
    """
    if len(parts) < 2:
        raise ValueError("A model needs at least one part")
    parts = sorted((dict(p) for p in parts), key=lambda p: p["elem_start"])
    k = next((i for i, p in enumerate(parts) if p["id"] == pid), None)
    if k is None:
        raise KeyError(pid)
    gone = parts[k]
    keep_mask = np.ones(len(elems), dtype=bool)
    keep_mask[gone["elem_start"]:gone["elem_end"]] = False
    new_elems = elems[keep_mask]
    used = np.unique(new_elems)
    remap = -np.ones(len(nodes), dtype=np.int64)
    remap[used] = np.arange(len(used))
    n_removed = gone["elem_end"] - gone["elem_start"]
    new_parts = []
    for i, p in enumerate(parts):
        if i == k:
            continue
        shift = n_removed if i > k else 0
        q = dict(p)
        q["elem_start"] -= shift
        q["elem_end"] -= shift
        new_parts.append(q)
    return nodes[used], remap[new_elems], new_parts, remap, n_removed


def remap_setup(project: Dict[str, Any], remap: np.ndarray, removed_part_id: str) -> Dict[str, Any]:
    """
    Remap node ids in BCs, loads, constraints, sets and history nodes after nodes were removed.
    Items whose selection became empty (or that referenced the removed part) are dropped.
    """
    out: Dict[str, Any] = {}
    bcs = []
    for bc in project.get("boundary_conditions") or []:
        bc = dict(bc)
        if bc.get("node_ids") or not bc.get("ref_id"):
            ids, _ = _map_ids(bc.get("node_ids") or [], remap)
            if not ids:
                continue
            bc["node_ids"] = ids
        bcs.append(bc)
    loads = []
    for ld in project.get("loads") or []:
        ld = dict(ld)
        if ld.get("faces"):
            ld["faces"] = _map_faces(ld["faces"], remap)
            if not ld["faces"]:
                continue
        if ld.get("node_ids"):
            ld["node_ids"], _ = _map_ids(ld["node_ids"], remap)
            if not ld["node_ids"]:
                continue
        loads.append(ld)
    cons = []
    for c in project.get("constraints") or []:
        c = dict(c)
        if removed_part_id in (c.get("master_part"), c.get("slave_part")):
            continue
        keep = True
        for key in ("faces", "slave_faces", "master_faces"):
            if c.get(key):
                c[key] = _map_faces(c[key], remap)
                keep = keep and bool(c[key])
        for key in ("node_ids", "slave_node_ids"):
            if c.get(key):
                c[key], _ = _map_ids(c[key], remap)
                keep = keep and bool(c[key])
        if keep:
            cons.append(c)
    sets = []
    for s in project.get("node_sets") or []:
        s = dict(s)
        if s.get("kind") == "faces":
            s["ids"] = _map_faces(s.get("ids"), remap)
        else:
            s["ids"], _ = _map_ids(s.get("ids") or [], remap)
        if s["ids"]:
            sets.append(s)
    settings = dict(project.get("settings") or {})
    if settings.get("history_nodes"):
        settings["history_nodes"], _ = _map_ids(settings["history_nodes"], remap)
    out.update(boundary_conditions=bcs, loads=loads, constraints=cons, node_sets=sets, settings=settings)
    return out
