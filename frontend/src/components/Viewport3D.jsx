import React, { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, Grid, GizmoHelper, GizmoViewport } from "@react-three/drei";
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { boundingBox, minMax } from "./utils.js";
import { glyphSize } from "./glyphs.js";

// FEA models are Z-up (gravity is -Z), so the whole scene is Z-up as well.
THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

// Blue -> cyan -> green -> yellow -> red contour map (same convention as PrePoMax/CalculiX viewers).
const STOPS = [
  [0.0, 0, 0, 0.8],
  [0.25, 0, 0.6, 1],
  [0.5, 0, 0.8, 0.27],
  [0.75, 1, 0.93, 0],
  [1.0, 0.93, 0, 0],
];
// Distinct, calm tints for parts (shown when no result contour is displayed).
export const PART_COLORS = [
  [0.56, 0.64, 0.80], [0.80, 0.62, 0.45], [0.50, 0.72, 0.58], [0.76, 0.55, 0.72],
  [0.82, 0.76, 0.46], [0.46, 0.70, 0.78], [0.78, 0.50, 0.50], [0.62, 0.62, 0.66],
];

export function colormap(t) {
  t = Math.min(1, Math.max(0, t));
  for (let i = 0; i < STOPS.length - 1; i++) {
    const [t0, r0, g0, b0] = STOPS[i];
    const [t1, r1, g1, b1] = STOPS[i + 1];
    if (t >= t0 && t <= t1) {
      const f = (t - t0) / (t1 - t0 || 1);
      return [r0 + (r1 - r0) * f, g0 + (g1 - g0) * f, b0 + (b1 - b0) * f];
    }
  }
  return [0.93, 0, 0];
}

/* ---------------------------------------------------------------- the mesh */
const SELECT_COLOR = [1, 0.17, 0.84]; // magenta: selected nodes (markers) and faces (tint)

function SurfaceMesh({
  nodes, faces, displacements, field, range, deformScale, animate, oscillate,
  showEdges, showUndeformed, planes, selectedFaces, hoverTris, onClickFace, scaleRef, faceParts,
  onHover, onHoverEnd,
}) {
  const base = useMemo(() => {
    const nv = faces.length * 3;
    const idx = new Int32Array(nv);
    const pos0 = new Float32Array(nv * 3);
    faces.forEach((tri, fi) =>
      tri.forEach((n, vi) => {
        const k = fi * 3 + vi;
        idx[k] = n;
        pos0.set(nodes[n], k * 3);
      })
    );
    return { idx, pos0, nv };
  }, [nodes, faces]);

  const dispVerts = useMemo(() => {
    if (!displacements || !displacements.length) return null;
    const a = new Float32Array(base.nv * 3);
    for (let k = 0; k < base.nv; k++) {
      const d = displacements[base.idx[k]];
      if (d) a.set(d, k * 3);
    }
    return a;
  }, [displacements, base]);

  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new THREE.BufferAttribute(base.pos0.slice(), 3);
    pos.setUsage(THREE.DynamicDrawUsage);
    g.setAttribute("position", pos);
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(base.nv * 3), 3));
    return g;
  }, [base]);

  const ghost = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(base.pos0, 3));
    return g;
  }, [base]);

  // exactly the selected triangles (by their three node ids), so the tint never bleeds onto neighbouring faces
  const selectedMask = useMemo(() => {
    const mask = new Uint8Array(faces.length);
    if (!selectedFaces || !selectedFaces.length) return mask;
    const key = (t) => [t[0], t[1], t[2]].sort((a, b) => a - b).join(",");
    const want = new Set(selectedFaces.map(key));
    faces.forEach((t, i) => { if (want.has(key(t))) mask[i] = 1; });
    return mask;
  }, [faces, selectedFaces]);

  // vertex colours: contour, or plain shading; selected faces override
  useEffect(() => {
    const col = geometry.getAttribute("color");
    let lo = 0;
    let hi = 1;
    if (field && field.length) {
      [lo, hi] = range || minMax(field);
      if (hi - lo < 1e-30) hi = lo + 1e-30;
    }
    for (let k = 0; k < base.nv; k++) {
      const n = base.idx[k];
      const fi = Math.floor(k / 3);
      let c;
      if (selectedMask[fi]) c = SELECT_COLOR;
      else if (field && field.length) c = colormap((field[n] - lo) / (hi - lo));
      else c = PART_COLORS[(faceParts ? faceParts[fi] : 0) % PART_COLORS.length];
      col.setXYZ(k, c[0], c[1], c[2]);
    }
    col.needsUpdate = true;
  }, [geometry, base, field, range, selectedMask, faceParts]);

  // outline of the selected faces: the edges that belong to exactly one selected triangle
  const outline = useMemo(() => {
    const count = new Map();
    const ends = new Map();
    for (let fi = 0; fi < faces.length; fi++) {
      if (!selectedMask[fi]) continue;
      for (let e = 0; e < 3; e++) {
        const a = faces[fi][e];
        const b = faces[fi][(e + 1) % 3];
        const key = a < b ? `${a}_${b}` : `${b}_${a}`;
        count.set(key, (count.get(key) || 0) + 1);
        ends.set(key, [fi * 3 + e, fi * 3 + ((e + 1) % 3)]);
      }
    }
    const pairs = [];
    count.forEach((c, key) => { if (c === 1) pairs.push(ends.get(key)); });
    if (!pairs.length) return null;
    const g = new THREE.BufferGeometry();
    const pos = new THREE.BufferAttribute(new Float32Array(pairs.length * 6), 3);
    pos.setUsage(THREE.DynamicDrawUsage);
    g.setAttribute("position", pos);
    return { geometry: g, pairs };
  }, [faces, selectedMask]);

  // the hover preview: the triangles (a whole quad on brick meshes) that a click would select
  const hoverGeometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new THREE.BufferAttribute(new Float32Array(6 * 3), 3);
    pos.setUsage(THREE.DynamicDrawUsage);
    g.setAttribute("position", pos);
    g.setDrawRange(0, 0);
    return g;
  }, []);

  const lastScale = useRef(null);
  useFrame(({ clock }) => {
    let s = deformScale;
    if (animate) {
      const w = clock.getElapsedTime() * Math.PI * 1.2;
      s = deformScale * (oscillate ? Math.sin(w) : 0.5 - 0.5 * Math.cos(w));
    }
    scaleRef.current = dispVerts ? s : 0;
    const p = geometry.getAttribute("position");
    if (!dispVerts) {
      if (lastScale.current !== 0) {
        p.array.set(base.pos0);
        p.needsUpdate = true;
        lastScale.current = 0;
      }
      return;
    }
    if (lastScale.current === s && !animate) return;
    const arr = p.array;
    for (let i = 0; i < arr.length; i++) arr[i] = base.pos0[i] + dispVerts[i] * s;
    p.needsUpdate = true;
    geometry.computeBoundingSphere();
    lastScale.current = s;
  });

  // geometry/data changed -> force a refresh on the next frame
  useEffect(() => {
    lastScale.current = null;
  }, [dispVerts, base]);

  // keep the selection outline and the hover preview on the (possibly deforming) surface; runs after the surface update above
  const overlayKey = useRef("");
  useFrame(() => {
    const p = geometry.getAttribute("position");
    const key = `${scaleRef.current}|${outline ? outline.geometry.id : "-"}|${hoverTris ? hoverTris.join(",") : "-"}|${animate}`;
    if (key === overlayKey.current && !animate) return;
    overlayKey.current = key;
    if (outline) {
      const oa = outline.geometry.getAttribute("position");
      outline.pairs.forEach(([i, j], q) => {
        for (let c = 0; c < 3; c++) { oa.array[q * 6 + c] = p.array[i * 3 + c]; oa.array[q * 6 + 3 + c] = p.array[j * 3 + c]; }
      });
      oa.needsUpdate = true;
    }
    if (hoverTris && hoverTris.length) {
      const ha = hoverGeometry.getAttribute("position");
      hoverTris.forEach((t, q) => { for (let k = 0; k < 9; k++) ha.array[q * 9 + k] = p.array[t * 9 + k]; });
      ha.needsUpdate = true;
      hoverGeometry.setDrawRange(0, hoverTris.length * 3);
    } else {
      hoverGeometry.setDrawRange(0, 0);
    }
  });

  return (
    <>
      <mesh geometry={geometry} onClick={onClickFace} onPointerMove={onHover} onPointerOut={onHoverEnd}>
        <meshStandardMaterial vertexColors side={THREE.DoubleSide} flatShading clippingPlanes={planes}
          polygonOffset polygonOffsetFactor={1} polygonOffsetUnits={1} />
      </mesh>
      {showEdges && (
        <mesh geometry={geometry}>
          <meshBasicMaterial color="#10141c" wireframe transparent opacity={0.35} clippingPlanes={planes} />
        </mesh>
      )}
      {outline && (
        <lineSegments geometry={outline.geometry} renderOrder={4} frustumCulled={false}>
          <lineBasicMaterial color="#2b0a3d" clippingPlanes={planes} polygonOffset polygonOffsetFactor={-2} polygonOffsetUnits={-2} />
        </lineSegments>
      )}
      <mesh geometry={hoverGeometry} renderOrder={3} frustumCulled={false}>
        <meshBasicMaterial color="#ffd166" transparent opacity={0.6} side={THREE.DoubleSide} clippingPlanes={planes}
          polygonOffset polygonOffsetFactor={-2} polygonOffsetUnits={-2} depthWrite={false} />
      </mesh>
      {showUndeformed && dispVerts && (
        <mesh geometry={ghost}>
          <meshBasicMaterial color="#6b7280" wireframe transparent opacity={0.5} clippingPlanes={planes} />
        </mesh>
      )}
    </>
  );
}

// Round, outlined sprite so probe / min / max markers are discs, not the default square points.
let discTexture = null;
function getDiscTexture() {
  if (discTexture || typeof document === "undefined") return discTexture;
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  g.beginPath(); g.arc(32, 32, 30, 0, Math.PI * 2); g.fillStyle = "#000"; g.fill();
  g.beginPath(); g.arc(32, 32, 24, 0, Math.PI * 2); g.fillStyle = "#fff"; g.fill();
  discTexture = new THREE.CanvasTexture(c);
  return discTexture;
}

/** Round markers that follow the (animated) deformation: probe, min / max. */
function PointLayer({ nodes, ids, displacements, color, size = 7, scaleRef, depthTest = false, opacity = 1, disc = true }) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(ids.length * 3), 3));
    return g;
  }, [ids]);
  useFrame(() => {
    const attr = geometry.getAttribute("position");
    const arr = attr.array;
    const s = scaleRef.current || 0;
    // only touch the GPU buffer when something moved (thousands of snapping dots would otherwise upload every frame)
    if (geometry.userData.scale === s && geometry.userData.disp === displacements) return;
    geometry.userData.scale = s;
    geometry.userData.disp = displacements;
    ids.forEach((id, k) => {
      const p = nodes[id];
      const d = displacements && displacements[id] ? displacements[id] : [0, 0, 0];
      arr[k * 3] = p[0] + d[0] * s;
      arr[k * 3 + 1] = p[1] + d[1] * s;
      arr[k * 3 + 2] = p[2] + d[2] * s;
    });
    attr.needsUpdate = true;
    geometry.computeBoundingSphere();
  });
  if (!ids.length) return null;
  return (
    <points geometry={geometry} renderOrder={10} frustumCulled={false}>
      <pointsMaterial color={color} size={size * 1.5} sizeAttenuation={false} depthTest={depthTest}
        map={disc ? getDiscTexture() : null} alphaTest={disc ? 0.5 : 0} transparent opacity={opacity} depthWrite={false} />
    </points>
  );
}

/* ------------------------------------------------ 3-D glyphs (arrows, cones, spheres) */
// All glyph geometries have their tip (or centre, for spheres) on the origin and point along +Z.
const GLYPH_GEOMETRY = (() => {
  const toZ = (g) => { g.rotateX(Math.PI / 2); return g; }; // three's cones/cylinders stand along +Y
  const head = toZ(new THREE.ConeGeometry(0.2, 0.42, 20)); head.translate(0, 0, -0.21);
  const shaft = toZ(new THREE.CylinderGeometry(0.058, 0.058, 0.62, 12)); shaft.translate(0, 0, -0.73);
  const cone = toZ(new THREE.ConeGeometry(0.3, 0.85, 22)); cone.translate(0, 0, -0.425);
  return {
    arrow: mergeGeometries([head, shaft]),
    cone,
    sphere: new THREE.SphereGeometry(0.2, 18, 14),
  };
})();
const Z_AXIS = new THREE.Vector3(0, 0, 1);

/** One colour, one shape, many positions: a single draw call however many nodes carry the glyph. */
function GlyphLayer({ layer, size }) {
  const ref = useRef();
  useEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const m = new THREE.Matrix4();
    const q = new THREE.Quaternion();
    const pos = new THREE.Vector3();
    const dir = new THREE.Vector3();
    const sc = new THREE.Vector3();
    const k = layer.shape === "cone" ? size * 0.75 : layer.shape === "sphere" ? size * 0.7 : size;
    layer.items.forEach((it, i) => {
      pos.set(it.p[0], it.p[1], it.p[2]);
      dir.set(it.d[0], it.d[1], it.d[2]);
      q.setFromUnitVectors(Z_AXIS, dir.lengthSq() ? dir.normalize() : Z_AXIS);
      m.compose(pos, q, sc.set(k, k, k));
      mesh.setMatrixAt(i, m);
    });
    mesh.count = layer.items.length;
    mesh.instanceMatrix.needsUpdate = true;
  }, [layer, size]);
  return (
    <instancedMesh ref={ref} args={[GLYPH_GEOMETRY[layer.shape], undefined, layer.items.length]}
      renderOrder={10} frustumCulled={false}>
      <meshStandardMaterial color={layer.color} roughness={0.45} metalness={0.1} depthTest={false} />
    </instancedMesh>
  );
}

/** Glyph layers for supports / loads. The size is shared so every symbol in the scene reads at one scale. */
function Glyphs({ layers, diag }) {
  const busiest = layers.reduce((n, l) => Math.max(n, l.shape === "cone" ? l.items.length / 2 : l.items.length), 0);
  const size = glyphSize(diag, busiest);
  return layers.map((l, i) => <GlyphLayer key={`${l.shape}-${l.color}-${i}`} layer={l} size={size} />);
}

/**
 * Selected nodes as small 3-D spheres (instanced, one draw call). They follow the deformation, are hidden when the node
 * is on the far side of the model, and stay visible on top for beams / bars where the node sits inside the drawn tube.
 */
function NodeMarkers({ nodes, ids, displacements, scaleRef, radius, color, onTop = false }) {
  const ref = useRef();
  const m = useMemo(() => new THREE.Matrix4(), []);
  useFrame(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const s = scaleRef.current || 0;
    ids.forEach((id, i) => {
      const p = nodes[id];
      if (!p) return;
      const d = displacements && displacements[id] ? displacements[id] : [0, 0, 0];
      const k = radius / 0.2; // GLYPH_GEOMETRY.sphere has radius 0.2
      m.makeScale(k, k, k);
      m.setPosition(p[0] + d[0] * s, p[1] + d[1] * s, p[2] + d[2] * s);
      mesh.setMatrixAt(i, m);
    });
    mesh.count = ids.length;
    mesh.instanceMatrix.needsUpdate = true;
  });
  if (!ids.length) return null;
  return (
    <instancedMesh ref={ref} key={ids.length} args={[GLYPH_GEOMETRY.sphere, undefined, ids.length]} renderOrder={12} frustumCulled={false}>
      <meshBasicMaterial color={color} depthTest={!onTop} />
    </instancedMesh>
  );
}

const AXIS_ARROWS = [
  { color: "#e03c3c", q: new THREE.Quaternion().setFromUnitVectors(Z_AXIS, new THREE.Vector3(1, 0, 0)), at: [1, 0, 0] },
  { color: "#3cb94b", q: new THREE.Quaternion().setFromUnitVectors(Z_AXIS, new THREE.Vector3(0, 1, 0)), at: [0, 1, 0] },
  { color: "#3c7de0", q: new THREE.Quaternion(), at: [0, 0, 1] },
];

/** The 3-D pick cursor: a hub with X / Y / Z arrows (red / green / blue) that follows the pointer over the model. */
function PickCursor({ position, size }) {
  if (!position) return null;
  return (
    <group position={position} scale={size} renderOrder={20}>
      <mesh geometry={GLYPH_GEOMETRY.sphere} scale={0.55} renderOrder={20}>
        <meshBasicMaterial color="#ffffff" depthTest={false} />
      </mesh>
      {AXIS_ARROWS.map((a) => (
        <mesh key={a.color} geometry={GLYPH_GEOMETRY.arrow} position={a.at} quaternion={a.q} renderOrder={20}>
          <meshBasicMaterial color={a.color} depthTest={false} />
        </mesh>
      ))}
    </group>
  );
}

function ReferencePoints({ refs, size }) {
  return refs.map((r) => (
    <mesh key={r.id} position={r.coords} renderOrder={11}>
      <sphereGeometry args={[size, 16, 12]} />
      <meshBasicMaterial color="#f59e0b" depthTest={false} />
    </mesh>
  ));
}

/* ----------------------------------------------------- camera & utilities */
const noRaycast = () => null;

/** Another model of the stack shown for reference: translucent, never pickable, never deformed. */
function GhostModel({ ghost, planes }) {
  const { surface, lines } = useMemo(() => {
    const pts = ghost.nodes || [];
    const tri = new Float32Array((ghost.surface_faces || []).length * 9);
    (ghost.surface_faces || []).forEach((t, i) => t.forEach((n, k) => tri.set(pts[n] || [0, 0, 0], i * 9 + k * 3)));
    const seg = new Float32Array((ghost.segments || []).length * 6);
    (ghost.segments || []).forEach((e, i) => { seg.set(pts[e[0]] || [0, 0, 0], i * 6); seg.set(pts[e[1]] || [0, 0, 0], i * 6 + 3); });
    const g1 = new THREE.BufferGeometry();
    g1.setAttribute("position", new THREE.BufferAttribute(tri, 3));
    g1.computeVertexNormals();
    const g2 = new THREE.BufferGeometry();
    g2.setAttribute("position", new THREE.BufferAttribute(seg, 3));
    return { surface: tri.length ? g1 : null, lines: seg.length ? g2 : null };
  }, [ghost]);
  useEffect(() => () => { surface?.dispose(); lines?.dispose(); }, [surface, lines]);
  return (
    <>
      {surface && (
        <mesh geometry={surface} raycast={noRaycast} renderOrder={-1}>
          <meshStandardMaterial color={ghost.color || "#8a94a6"} transparent opacity={0.13} depthWrite={false}
            clippingPlanes={planes} />
        </mesh>
      )}
      {lines && (
        <lineSegments geometry={lines} raycast={noRaycast}>
          <lineBasicMaterial color={ghost.color || "#8a94a6"} transparent opacity={0.6} clippingPlanes={planes} />
        </lineSegments>
      )}
    </>
  );
}

function Rig({ bbox, viewCommand, onApi }) {
  const { camera, gl, scene, controls } = useThree();
  const center = useMemo(() => bbox.lo.map((v, i) => v + bbox.size[i] / 2), [bbox]);
  const diag = Math.max(Math.hypot(...bbox.size), 0.1);

  useEffect(() => {
    camera.near = diag / 500;
    camera.far = diag * 100;
    camera.updateProjectionMatrix();
  }, [camera, diag]);

  useEffect(() => {
    const d = diag * 1.1;
    const [cx, cy, cz] = center;
    const views = {
      iso: [cx + d * 0.7, cy - d * 0.9, cz + d * 0.65],
      front: [cx, cy - d * 1.4, cz],
      back: [cx, cy + d * 1.4, cz],
      right: [cx + d * 1.4, cy, cz],
      left: [cx - d * 1.4, cy, cz],
      // offset along -y only, so the screen's up axis is +y and x runs left to right (an x offset would rotate the view)
      top: [cx, cy - d * 0.001, cz + d * 1.4],
      bottom: [cx, cy + d * 0.001, cz - d * 1.4],
    };
    const p = views[viewCommand?.name || "iso"] || views.iso;
    camera.position.set(p[0], p[1], p[2]);
    camera.up.set(0, 0, 1);
    camera.lookAt(cx, cy, cz);
    if (controls) {
      controls.target.set(cx, cy, cz);
      controls.update();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewCommand, center, diag, controls]);

  useEffect(() => {
    if (!onApi) return;
    onApi({
      capture: () => {
        gl.render(scene, camera);
        return gl.domElement.toDataURL("image/png");
      },
    });
  }, [gl, scene, camera, onApi]);
  return null;
}

/** Toolbar pictograms (inline SVG so they inherit the button colour and stay crisp at any zoom). */
/** Hands the camera and canvas to the component outside the <Canvas> (box selection projects nodes with them). */
function ThreeBridge({ target }) {
  const { camera, gl } = useThree();
  target.current = { camera, gl };
  return null;
}

// In the selection tools left-drag draws the selection box, so the view turns with the right button and pans with the middle.
const ORBIT_BUTTONS = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
const SELECT_BUTTONS = { LEFT: -1, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.ROTATE };
const DRAG_START = 4; // px of movement before a press becomes a box instead of a click

/**
 * Box selection. Projects the visible surface to the screen and returns what lies in the rectangle (client coords):
 * dragging to the right is a window (faces must lie fully inside), to the left a crossing (any corner inside).
 * Unless `through` is set only faces turned towards the camera count, so nodes / faces on the far side are skipped.
 * Returns { nodes: view node ids, faces: indices into `faces` }.
 */
function boxPick({ rect, crossing, nodes, faces, displacements, scale, surfaceMid, camera, canvas, planes, cull, wantFaces }) {
  const r = canvas.getBoundingClientRect();
  const x0 = Math.min(rect.x0, rect.x1), x1 = Math.max(rect.x0, rect.x1);
  const y0 = Math.min(rect.y0, rect.y1), y1 = Math.max(rect.y0, rect.y1);
  const v = new THREE.Vector3();
  const world = (n) => {
    const p = nodes[n];
    const d = displacements && displacements[n];
    return v.set(p[0] + (d ? d[0] * scale : 0), p[1] + (d ? d[1] * scale : 0), p[2] + (d ? d[2] * scale : 0));
  };
  const memo = new Map();          // node -> inside the box (and not cut away by the section plane)
  const inside = (n) => {
    if (memo.has(n)) return memo.get(n);
    let ok = !!nodes[n];
    if (ok) {
      const w = world(n);
      if (planes.some((pl) => pl.distanceToPoint(w) < 0)) ok = false;
      else {
        w.project(camera);
        const sx = r.left + ((w.x + 1) / 2) * r.width;
        const sy = r.top + ((1 - w.y) / 2) * r.height;
        ok = w.z > -1 && w.z < 1 && sx >= x0 && sx <= x1 && sy >= y0 && sy <= y1;
      }
    }
    memo.set(n, ok);
    return ok;
  };
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const facing = (t) => {
    a.copy(world(t[0])); b.copy(world(t[1])); c.copy(world(t[2]));
    const nrm = b.sub(a).cross(c.sub(a));
    return nrm.dot(a.sub(camera.position)) < 0;
  };
  const outNodes = new Set();
  const outFaces = [];
  faces.forEach((t, fi) => {
    if (cull && !facing(t)) return;
    if (wantFaces) {
      const hits = t.filter(inside).length;
      if (crossing ? hits > 0 : hits === 3) outFaces.push(fi);
    } else {
      t.forEach((n) => { if (inside(n)) outNodes.add(n); });
      (surfaceMid?.[fi] || []).forEach((m) => { if (m >= 0 && inside(m)) outNodes.add(m); });
    }
  });
  return { nodes: [...outNodes], faces: outFaces };
}

function ToolIcon({ name }) {
  const common = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7,
    strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
  switch (name) {
    case "orbit": // two arrows circling a model
      return (<svg {...common}><ellipse cx="12" cy="12" rx="9" ry="4.2" transform="rotate(-25 12 12)" /><path d="M19 6.5l1.5 3.3-3.5.2" /><circle cx="12" cy="12" r="2.2" fill="currentColor" /></svg>);
    case "node": // pointer arrow landing on a node
      return (<svg {...common}><path d="M5 3l12 6.2-5.3 1.6L9.6 16z" fill="currentColor" fillOpacity=".15" /><circle cx="18" cy="18" r="2.6" fill="currentColor" /></svg>);
    case "face": // a highlighted face of a cube
      return (<svg {...common}><path d="M12 3l8 4v10l-8 4-8-4V7z" /><path d="M12 11v10M4 7l8 4 8-4" /><path d="M12 11l8-4v10l-8 4z" fill="currentColor" fillOpacity=".35" /></svg>);
    case "angle": // faces that meet at an angle
      return (<svg {...common}><path d="M3 18h10l6-9" /><path d="M13 18a6 6 0 0 0-1.4-3.8" /><path d="M3 18V6l9 0" strokeDasharray="2 2.4" /></svg>);
    default:
      return null;
  }
}

const GRID_COLORS = {
  light: { cell: "#aab4c4", section: "#7f8a9c", bg: "linear-gradient(#f7f9fc, #b9c3d3)" },
  dark: { cell: "#3a4150", section: "#566075", bg: "linear-gradient(#2a303a, #12151a)" },
};

const niceStep = (x) => {
  const p = 10 ** Math.floor(Math.log10(Math.max(x, 1e-12)));
  const m = x / p;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
};

export default function Viewport3D({
  nodes, faces, displacements, field, range = null, deformScale = 0, animate = false, oscillate = false,
  showEdges = false, showUndeformed = false, clip = null, showMinMax = false,
  selectedNodes = [], selectedFaces = [], onPickFace, onPickNode, probeMode = false, onProbe, probeNode = null,
  glyphs = [], refPoints = [], theme = "dark", pickMode = "none", setPickMode, allowFacePick = false,
  viewCommand, setView, onApi, toolbarExtra, faceParts = null, pickNearest = false,
  surfaceMid = null, faceSiblings = null, ghosts = [], onBoxSelect, cullBack = true,
}) {
  const colors = GRID_COLORS[theme] || GRID_COLORS.dark;
  const bbox = useMemo(() => boundingBox(nodes), [nodes]);
  const scaleRef = useRef(0);
  const diag = Math.max(Math.hypot(...bbox.size), 0.1);

  const planes = useMemo(() => {
    if (!clip?.enabled) return [];
    const k = clip.axis ?? 0;
    const pos = bbox.lo[k] + (clip.pos ?? 0.5) * bbox.size[k];
    const n = [0, 0, 0];
    n[k] = clip.flip ? 1 : -1;
    return [new THREE.Plane(new THREE.Vector3(...n), clip.flip ? -pos : pos)];
  }, [clip, bbox]);

  const minMaxIds = useMemo(() => {
    if (!showMinMax || !field || !field.length) return { lo: [], hi: [] };
    let lo = -1;
    let hi = -1;
    let vlo = Infinity;
    let vhi = -Infinity;
    const seen = new Set();
    faces.forEach((t) => t.forEach((n) => {
      if (seen.has(n)) return;
      seen.add(n);
      if (field[n] < vlo) { vlo = field[n]; lo = n; }
      if (field[n] > vhi) { vhi = field[n]; hi = n; }
    }));
    return { lo: lo >= 0 ? [lo] : [], hi: hi >= 0 ? [hi] : [] };
  }, [showMinMax, field, faces]);

  /**
   * The mesh node closest to the ray hit point, among the corners of the hit triangle and - on quadratic meshes - the
   * mid-side nodes at the centre of its edges. Returns the node id and where it is drawn (deformed position included).
   */
  const nearestNode = (e) => {
    const fi = e.faceIndex;
    const tri = faces[fi];
    const pos = e.object.geometry.getAttribute("position");
    const s = scaleRef.current || 0;
    const cand = tri.map((n, vi) => [n, pos.getX(fi * 3 + vi), pos.getY(fi * 3 + vi), pos.getZ(fi * 3 + vi)]);
    const mids = surfaceMid && surfaceMid[fi];
    if (mids) {
      mids.forEach((m) => {
        const p = m >= 0 ? nodes[m] : null;
        if (!p) return;
        const d = displacements && displacements[m] ? displacements[m] : null;
        cand.push([m, p[0] + (d ? d[0] * s : 0), p[1] + (d ? d[1] * s : 0), p[2] + (d ? d[2] * s : 0)]);
      });
    }
    let best = cand[0];
    let bd = Infinity;
    cand.forEach((c) => {
      const dd = (c[1] - e.point.x) ** 2 + (c[2] - e.point.y) ** 2 + (c[3] - e.point.z) ** 2;
      if (dd < bd) { bd = dd; best = c; }
    });
    return { id: best[0], at: [best[1], best[2], best[3]] };
  };

  // 3-D pick cursor: snaps to the nearest node in node/probe mode, follows the surface in face modes.
  // In the face modes the face (a whole quad on brick meshes) that a click would select is previewed as well.
  const [cursor, setCursor] = useState(null);
  const [hoverTris, setHoverTris] = useState(null);
  const picking = pickMode !== "none" || probeMode;
  const faceMode = pickMode === "select-faces" || pickMode === "select-angle";
  const handleHover = (e) => {
    if (!picking || e.faceIndex === undefined || e.faceIndex === null) return;
    e.stopPropagation();
    const at = pickMode === "select" || probeMode ? nearestNode(e).at : [e.point.x, e.point.y, e.point.z];
    setCursor((c) => (c && c[0] === at[0] && c[1] === at[1] && c[2] === at[2] ? c : at));
    if (faceMode) {
      const sib = faceSiblings ? faceSiblings[e.faceIndex] : -1;
      const next = sib >= 0 ? [e.faceIndex, sib] : [e.faceIndex];
      setHoverTris((h) => (h && h.length === next.length && h.every((v, i) => v === next[i]) ? h : next));
    } else if (hoverTris) {
      setHoverTris(null);
    }
  };
  const clearCursor = () => { setCursor(null); setHoverTris(null); };
  useEffect(() => { if (!picking) { setCursor(null); setHoverTris(null); } }, [picking]);
  useEffect(() => { setHoverTris(null); }, [faces, pickMode]);

  // ------------------------------------------------------------ box selection
  const three = useRef(null);
  const drag = useRef(null);
  const suppressClick = useRef(false);
  const [box, setBox] = useState(null);
  const [through, setThrough] = useState(false);
  const boxMode = !!onBoxSelect && !probeMode && (pickMode === "select" || faceMode);
  const onBoxDown = (e) => {
    if (!boxMode || e.button !== 0 || e.target.tagName !== "CANVAS") return;
    suppressClick.current = false;
    drag.current = { x0: e.clientX, y0: e.clientY, active: false };
  };
  const onBoxMove = (e) => {
    const d = drag.current;
    if (!d) return;
    if (!d.active) {
      if (Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < DRAG_START) return;
      d.active = true;
      e.currentTarget.setPointerCapture(e.pointerId);
      setCursor(null);
      setHoverTris(null);
    }
    setBox({ x0: d.x0, y0: d.y0, x1: e.clientX, y1: e.clientY });
  };
  const onBoxUp = (e) => {
    const d = drag.current;
    drag.current = null;
    if (!d?.active) return;
    suppressClick.current = true;            // the click that follows this release must not also pick a node
    setBox(null);
    const rect = { x0: d.x0, y0: d.y0, x1: e.clientX, y1: e.clientY };
    if (!three.current) return;
    const picked = boxPick({
      rect, crossing: rect.x1 < rect.x0, nodes, faces, displacements, scale: scaleRef.current || 0, surfaceMid,
      camera: three.current.camera, canvas: three.current.gl.domElement, planes, cull: cullBack && !through, wantFaces: faceMode,
    });
    onBoxSelect(picked, e.shiftKey ? "add" : e.ctrlKey || e.metaKey ? "remove" : "replace", faceMode ? "faces" : "nodes");
  };
  useEffect(() => {
    if (!box) return undefined;
    const esc = (e) => { if (e.key === "Escape") { drag.current = null; setBox(null); } };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [box]);

  const handleClick = (e) => {
    e.stopPropagation();
    if (suppressClick.current) { suppressClick.current = false; return; }
    const fi = e.faceIndex;
    if (fi === undefined || fi === null) return;
    if (probeMode && onProbe) {
      onProbe(nearestNode(e).id);
      return;
    }
    if (pickMode === "select") {
      // one click, one node: the one the cursor is snapped to (beams / bars: the end node nearest to the click)
      onPickNode?.(nearestNode(e).id, e.shiftKey);
      return;
    }
    if (onPickFace && pickMode !== "none") {
      const sib = faceSiblings ? faceSiblings[fi] : -1;
      onPickFace(faces[fi], e.shiftKey, fi, sib >= 0 ? faces[sib] : null);
    }
  };

  // selection markers scale with the mesh: a fraction of the average edge length of the visible surface
  const markerRadius = useMemo(() => {
    let sum = 0;
    let cnt = 0;
    const step = Math.max(1, Math.floor(faces.length / 400));
    for (let i = 0; i < faces.length; i += step) {
      const t = faces[i];
      for (let k = 0; k < 3; k++) {
        const a = nodes[t[k]];
        const b = nodes[t[(k + 1) % 3]];
        if (!a || !b) continue;
        sum += Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
        cnt += 1;
      }
    }
    const edge = cnt ? sum / cnt : diag * 0.05;
    return Math.min(diag * 0.02, Math.max(diag * 0.004, edge * 0.13));
  }, [faces, nodes, diag]);

  // in node mode every surface node (corners and mid-side nodes) is drawn as a faint dot: these are the snapping targets
  const pickableIds = useMemo(() => {
    if (pickMode !== "select" || pickNearest) return [];
    const s = new Set();
    faces.forEach((t) => t.forEach((n) => s.add(n)));
    if (surfaceMid) surfaceMid.forEach((tm) => tm.forEach((m) => { if (m >= 0) s.add(m); }));
    return s.size <= 40000 ? Array.from(s) : [];
  }, [pickMode, pickNearest, faces, surfaceMid]);

  return (
    <>
      {setPickMode && (
        <div className="viewport-toolbar">
          <button className={`toolbar-btn ${pickMode === "none" && !probeMode ? "active" : ""}`}
            title="Orbit / pan / zoom (no selection)" onClick={() => setPickMode("none")}><ToolIcon name="orbit" /></button>
          <button className={`toolbar-btn ${pickMode === "select" ? "active" : ""}`}
            title="Select nodes: click the node the cursor snaps to (shift = add / remove). Quadratic meshes also offer the mid-side node at the centre of each edge."
            onClick={() => setPickMode("select")}><ToolIcon name="node" /></button>
          <button className={`toolbar-btn ${pickMode === "select-faces" ? "active" : ""}`}
            title="Select faces: the highlighted face is selected on click (shift = add / remove)" disabled={!allowFacePick}
            onClick={() => setPickMode("select-faces")}><ToolIcon name="face" /></button>
          <button className={`toolbar-btn ${pickMode === "select-angle" ? "active" : ""}`}
            title="Select connected faces by angle (set the angle in the Selection panel)" disabled={!allowFacePick}
            onClick={() => setPickMode("select-angle")}><ToolIcon name="angle" /></button>
          {onBoxSelect && (
            <button className={`toolbar-btn ${through ? "active" : ""}`} style={{ fontSize: 11 }} disabled={!cullBack}
              title={through ? "Box selection also takes the hidden far side of the model (click for visible side only)"
                : "Box selection takes only the side of the model facing you (click to select through)"}
              onClick={() => setThrough((t) => !t)}>Through</button>
          )}
          <span className="vt-sep" />
          {[["iso", "ISO"], ["front", "Front"], ["right", "Right"], ["top", "Top"]].map(([n, l]) => (
            <button key={n} className="toolbar-btn" style={{ fontSize: 11 }} title={`${l} view`}
              onClick={() => setView?.(n)}>{l}</button>
          ))}
          <button className="toolbar-btn" style={{ fontSize: 11 }} title="Fit all" onClick={() => setView?.("iso")}>Fit</button>
          <span className="vt-sep" />
          {toolbarExtra}
        </div>
      )}
      <div className="vp-canvas" onPointerDown={onBoxDown} onPointerMove={onBoxMove} onPointerUp={onBoxUp}
        onPointerCancel={() => { drag.current = null; setBox(null); }}>
      <Canvas camera={{ fov: 45, up: [0, 0, 1] }}
        style={{ background: colors.bg, cursor: picking ? "crosshair" : "grab" }}
        gl={{ preserveDrawingBuffer: true, localClippingEnabled: true }}>
        <ambientLight intensity={0.75} />
        <directionalLight position={[5, -8, 9]} intensity={0.8} />
        <directionalLight position={[-5, 6, -4]} intensity={0.3} />
        <Rig bbox={bbox} viewCommand={viewCommand} onApi={onApi} />
        <SurfaceMesh
          nodes={nodes} faces={faces} displacements={displacements} field={field} range={range}
          deformScale={deformScale} animate={animate} oscillate={oscillate} showEdges={showEdges}
          showUndeformed={showUndeformed} planes={planes} selectedFaces={selectedFaces} hoverTris={hoverTris}
          onClickFace={handleClick} scaleRef={scaleRef} faceParts={faceParts}
          onHover={handleHover} onHoverEnd={clearCursor}
        />
        {ghosts.map((g) => <GhostModel key={g.id} ghost={g} planes={planes} />)}
        <Glyphs layers={glyphs} diag={diag} />
        <PointLayer nodes={nodes} ids={pickableIds} displacements={displacements} color={theme === "dark" ? "#e2e8f0" : "#1e293b"}
          size={3} scaleRef={scaleRef} depthTest disc={false} opacity={0.85} />
        <NodeMarkers nodes={nodes} ids={selectedNodes} displacements={displacements} scaleRef={scaleRef} radius={markerRadius}
          color="#ff2bd6" onTop={pickNearest} />
        <PickCursor position={cursor} size={Math.min(diag * 0.05, Math.max(diag * 0.018, markerRadius * 6))} />
        <PointLayer nodes={nodes} ids={minMaxIds.lo} displacements={displacements} color="#2563eb" size={13} scaleRef={scaleRef} />
        <PointLayer nodes={nodes} ids={minMaxIds.hi} displacements={displacements} color="#dc2626" size={13} scaleRef={scaleRef} />
        <PointLayer nodes={nodes} ids={probeNode !== null && probeNode !== undefined ? [probeNode] : []}
          displacements={displacements} color="#facc15" size={14} scaleRef={scaleRef} />
        <ReferencePoints refs={refPoints} size={diag * 0.012} />
        <Grid args={[20, 20]} rotation={[Math.PI / 2, 0, 0]}
          position={[bbox.lo[0] + bbox.size[0] / 2, bbox.lo[1] + bbox.size[1] / 2, bbox.lo[2] - diag * 0.002]}
          cellSize={niceStep(diag / 20)} sectionSize={niceStep(diag / 20) * 5}
          cellColor={colors.cell} sectionColor={colors.section} infiniteGrid fadeDistance={diag * 8} />
        <GizmoHelper alignment="top-right" margin={[64, 100]}>
          <GizmoViewport axisColors={["#e05a5a", "#5ac25a", "#4d8fe0"]} labelColor="white" />
        </GizmoHelper>
        <OrbitControls makeDefault mouseButtons={boxMode ? SELECT_BUTTONS : ORBIT_BUTTONS} />
        <ThreeBridge target={three} />
      </Canvas>
      </div>
      {box && (
        <div className={`vp-box ${box.x1 < box.x0 ? "crossing" : "window"}`} style={{
          left: Math.min(box.x0, box.x1), top: Math.min(box.y0, box.y1),
          width: Math.abs(box.x1 - box.x0), height: Math.abs(box.y1 - box.y0) }} />
      )}
      {boxMode && !box && (
        <div className="vp-hint">
          Click to pick · drag a box: → inside only, ← touching · Shift add · Ctrl remove · right-drag rotate · middle-drag pan
        </div>
      )}
    </>
  );
}
