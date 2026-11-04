import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { FaceMeta, MeshData } from "../types";

export interface PickHit { key: string; face: FaceMeta | null; point: THREE.Vector3; triangle: number }

interface Entry {
  key: string;
  group: THREE.Group;
  faces?: FaceMeta[];
  mesh?: THREE.Mesh;
  hi?: THREE.Mesh;
  pickable: boolean;
}

/** Jet-like colour map, t in [0,1]. */
export function colormap(t: number): [number, number, number] {
  const stops: [number, number, number][] = [
    [0.169, 0.227, 0.604], [0.165, 0.643, 0.839], [0.247, 0.827, 0.541],
    [0.949, 0.886, 0.29], [0.961, 0.439, 0.173], [0.847, 0.122, 0.227],
  ];
  const x = Math.min(Math.max(t, 0), 1) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  const f = x - i;
  const a = stops[i], b = stops[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

/**
 * Thin Three.js wrapper (Z-up, millimetres).  The React layer owns *what* is shown,
 * this class owns *how*: lights, grid, orbit camera, picking and cleanup.
 */
export class Viewer {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  private entries = new Map<string, Entry>();
  private root = new THREE.Group();
  private grid!: THREE.GridHelper;
  private axes: THREE.AxesHelper;
  private ray = new THREE.Raycaster();
  private raf = 0;
  private ro: ResizeObserver;
  private themeObs: MutationObserver;
  private gridSize = 200;
  private downAt = { x: 0, y: 0 };
  private pickCb: ((h: PickHit | null, ev: MouseEvent) => void) | null = null;
  private wire = false;
  private dirty = true;
  private animate: ((t: number) => void) | null = null;

  constructor(private el: HTMLElement) {
    THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0); // transparent: the CSS viewport gradient shows through
    el.appendChild(this.renderer.domElement);

    this.camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100000);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(120, -160, 110);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.addEventListener("change", () => (this.dirty = true));

    this.scene.add(this.root);
    this.scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x25272d, 0.85));
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(150, -200, 300);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0x9fb8ff, 0.35);
    fill.position.set(-200, 120, 60);
    this.scene.add(fill);

    this.axes = new THREE.AxesHelper(30);
    this.scene.add(this.axes);
    this.makeGrid(200);

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(el);
    this.themeObs = new MutationObserver(() => { this.makeGrid(this.gridSize); this.dirty = true; });
    this.themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    this.resize();

    const dom = this.renderer.domElement;
    dom.addEventListener("pointerdown", (e) => (this.downAt = { x: e.clientX, y: e.clientY }));
    dom.addEventListener("pointerup", (e) => {
      if (Math.hypot(e.clientX - this.downAt.x, e.clientY - this.downAt.y) > 4 || e.button !== 0) return;
      this.handlePick(e);
    });
    const loop = (t: number) => {
      this.raf = requestAnimationFrame(loop);
      if (this.animate) { this.animate(t); this.dirty = true; }
      const moved = this.controls.update();
      if (this.dirty || moved) {
        this.renderer.render(this.scene, this.camera);
        this.dirty = false;
      }
    };
    this.raf = requestAnimationFrame(loop);
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.themeObs.disconnect();
    this.clear();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  invalidate() { this.dirty = true; }
  onPick(cb: (h: PickHit | null, ev: MouseEvent) => void) { this.pickCb = cb; }
  setAnimation(fn: ((t: number) => void) | null) { this.animate = fn; this.dirty = true; }

  private resize() {
    const w = Math.max(this.el.clientWidth, 1), h = Math.max(this.el.clientHeight, 1);
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  private makeGrid(size: number) {
    if (this.grid) { this.scene.remove(this.grid); this.grid.geometry.dispose(); }
    const step = size >= 1000 ? 100 : size >= 400 ? 50 : size >= 150 ? 20 : 10;
    const n = Math.max(2, Math.round(size / step));
    this.gridSize = size;
    // same grid colours as the Gearhead FEA viewport
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    this.grid = new THREE.GridHelper(n * step, n, dark ? 0x566075 : 0x7f8a9c, dark ? 0x3a4150 : 0xaab4c4);
    this.grid.rotation.x = Math.PI / 2; // lie in XY
    this.scene.add(this.grid);
    this.axes.scale.setScalar(Math.max(size / 8, 10) / 30);
  }

  setGridVisible(v: boolean) { this.grid.visible = v; this.dirty = true; }
  setWireframe(v: boolean) {
    this.wire = v;
    this.entries.forEach((e) => e.mesh && ((e.mesh.material as THREE.MeshStandardMaterial).wireframe = v));
    this.dirty = true;
  }

  // ------------------------------------------------------------ content
  clear() {
    this.entries.forEach((e) => this.disposeEntry(e));
    this.entries.clear();
    this.dirty = true;
  }

  remove(key: string) {
    const e = this.entries.get(key);
    if (e) { this.disposeEntry(e); this.entries.delete(key); this.dirty = true; }
  }

  private disposeEntry(e: Entry) {
    this.root.remove(e.group);
    e.group.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.geometry) m.geometry.dispose();
      const mat = m.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(mat)) mat.forEach((x) => x.dispose());
      else mat?.dispose();
    });
  }

  private put(e: Entry) {
    this.remove(e.key);
    this.entries.set(e.key, e);
    this.root.add(e.group);
    this.dirty = true;
  }

  /** Shaded B-rep tessellation with feature edges. `matrix` is a row-major 4x4 (assembly pose). */
  setSolid(key: string, m: MeshData, o: { color?: string; opacity?: number; matrix?: number[]; edges?: boolean; pickable?: boolean } = {}) {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(m.positions, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(m.normals, 3));
    g.setIndex(m.indices);
    g.computeBoundingSphere();
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(o.color ?? "#8aa4c8"), metalness: 0.15, roughness: 0.55, side: THREE.DoubleSide,
      transparent: (o.opacity ?? 1) < 1, opacity: o.opacity ?? 1, wireframe: this.wire,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
    });
    const mesh = new THREE.Mesh(g, mat);
    const group = new THREE.Group();
    group.add(mesh);
    if (o.edges !== false && m.edges?.length) {
      const pos: number[] = [];
      for (const e of m.edges) {
        const p = e.points;
        for (let i = 0; i + 5 < p.length; i += 3) pos.push(p[i], p[i + 1], p[i + 2], p[i + 3], p[i + 4], p[i + 5]);
      }
      const lg = new THREE.BufferGeometry();
      lg.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      group.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ color: 0x0b0d10 })));
    }
    if (o.matrix) {
      const M = new THREE.Matrix4().set(...(o.matrix as [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number]));
      group.matrixAutoUpdate = false;
      group.matrix.copy(M);
    }
    this.put({ key, group, faces: m.faces, mesh, pickable: o.pickable !== false });
  }

  /** Highlight faces of an entry with a translucent amber overlay. */
  highlightFaces(key: string, indices: number[], color = 0xf5a623) {
    const e = this.entries.get(key);
    if (!e || !e.mesh || !e.faces) return;
    if (e.hi) { e.group.remove(e.hi); e.hi.geometry.dispose(); (e.hi.material as THREE.Material).dispose(); e.hi = undefined; }
    if (!indices.length) { this.dirty = true; return; }
    const src = e.mesh.geometry;
    const idx = src.getIndex()!;
    const out: number[] = [];
    for (const fi of indices) {
      const f = e.faces.find((x) => x.index === fi);
      if (!f) continue;
      for (let t = f.tri_start; t < f.tri_start + f.tri_count; t++) out.push(idx.getX(t * 3), idx.getX(t * 3 + 1), idx.getX(t * 3 + 2));
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", src.getAttribute("position"));
    g.setAttribute("normal", src.getAttribute("normal"));
    g.setIndex(out);
    const mat = new THREE.MeshBasicMaterial({
      color, transparent: true, opacity: 0.55, side: THREE.DoubleSide, depthTest: true,
      polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
    });
    e.hi = new THREE.Mesh(g, mat);
    e.hi.raycast = () => {};
    e.group.add(e.hi);
    this.dirty = true;
  }

  /** Surface mesh coloured by a scalar field (FEA results), optional displacement for deformed shape. */
  setField(key: string, s: { positions: number[]; indices: number[]; values: number[]; min: number; max: number; displacements?: number[]; scale?: number }) {
    const n = s.positions.length / 3;
    const pos = new Float32Array(s.positions);
    const col = new Float32Array(n * 3);
    const span = s.max - s.min || 1;
    for (let i = 0; i < n; i++) {
      const c = colormap((s.values[i] - s.min) / span);
      col[i * 3] = c[0]; col[i * 3 + 1] = c[1]; col[i * 3 + 2] = c[2];
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos.slice(), 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    g.setIndex(s.indices);
    g.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({ vertexColors: true, side: THREE.DoubleSide, metalness: 0, roughness: 0.8, wireframe: this.wire });
    const mesh = new THREE.Mesh(g, mat);
    const group = new THREE.Group();
    group.add(mesh);
    this.put({ key, group, mesh, pickable: false });
    this.setDeformation(key, pos, s.displacements, s.scale ?? 0);
  }

  private setDeformation(key: string, base: Float32Array, disp: number[] | undefined, scale: number) {
    const e = this.entries.get(key);
    if (!e?.mesh) return;
    e.mesh.userData = { base, disp };
    this.applyDeformation(key, scale);
  }

  applyDeformation(key: string, scale: number) {
    const e = this.entries.get(key);
    if (!e?.mesh) return;
    const { base, disp } = e.mesh.userData as { base: Float32Array; disp?: number[] };
    const attr = e.mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    for (let i = 0; i < base.length; i++) attr.array[i] = base[i] + (disp ? disp[i] * scale : 0);
    attr.needsUpdate = true;
    e.mesh.geometry.computeVertexNormals();
    e.mesh.geometry.computeBoundingSphere();
    this.dirty = true;
  }

  /** Coloured line segments: flat list [x0,y0,z0,x1,y1,z1,...]. */
  setSegments(key: string, positions: number[], color: string | number, opacity = 1) {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    const l = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity }));
    const group = new THREE.Group();
    group.add(l);
    this.put({ key, group, pickable: false });
  }

  /** Height-field surface (stock simulation), error[] colours gouge (red) / leftover (blue). */
  setHeightfield(key: string, grid: { nx: number; ny: number; x0: number; y0: number; dx: number; dy: number; heights: number[]; error: number[] }, mode: "height" | "error") {
    const { nx, ny } = grid;
    const pos = new Float32Array(nx * ny * 3);
    const col = new Float32Array(nx * ny * 3);
    let zmin = Infinity, zmax = -Infinity;
    for (const h of grid.heights) { zmin = Math.min(zmin, h); zmax = Math.max(zmax, h); }
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i;
        pos[k * 3] = grid.x0 + i * grid.dx;
        pos[k * 3 + 1] = grid.y0 + j * grid.dy;
        pos[k * 3 + 2] = grid.heights[k];
        let c: [number, number, number];
        if (mode === "error") {
          const er = grid.error[k];
          if (er < -0.02) c = [0.9, 0.15, 0.2];         // gouge: cut deeper than model
          else if (er > 0.02) c = [0.2, 0.45, 0.95];   // leftover material
          else c = [0.35, 0.8, 0.5];
        } else c = colormap((grid.heights[k] - zmin) / (zmax - zmin || 1));
        col.set(c, k * 3);
      }
    }
    const idx: number[] = [];
    for (let j = 0; j < ny - 1; j++)
      for (let i = 0; i < nx - 1; i++) {
        const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
        idx.push(a, b, c, b, d, c);
      }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ vertexColors: true, side: THREE.DoubleSide, roughness: 0.7 }));
    const group = new THREE.Group();
    group.add(mesh);
    this.put({ key, group, mesh, pickable: false });
  }

  setBox(key: string, min: number[], max: number[], color = 0xf5a623) {
    const b = new THREE.Box3(new THREE.Vector3(...(min as [number, number, number])), new THREE.Vector3(...(max as [number, number, number])));
    const g = new THREE.Group();
    g.add(new THREE.Box3Helper(b, new THREE.Color(color)));
    this.put({ key, group: g, pickable: false });
  }

  setVisible(key: string, v: boolean) {
    const e = this.entries.get(key);
    if (e) { e.group.visible = v; this.dirty = true; }
  }

  /** Small marker (e.g. selected point). */
  setMarker(key: string, p: number[] | null, size = 2, color = 0xf5a623) {
    this.remove(key);
    if (!p) return;
    const m = new THREE.Mesh(new THREE.SphereGeometry(size, 16, 12), new THREE.MeshBasicMaterial({ color, depthTest: false }));
    m.position.set(p[0], p[1], p[2]);
    m.renderOrder = 10;
    const group = new THREE.Group();
    group.add(m);
    this.put({ key, group, pickable: false });
  }

  // ------------------------------------------------------------ camera
  bounds(): THREE.Box3 {
    const box = new THREE.Box3();
    this.entries.forEach((e) => { if (e.group.visible) box.expandByObject(e.group); });
    return box;
  }

  fit(pad = 1.05) {
    const box = this.bounds();
    if (box.isEmpty()) { this.makeGrid(200); this.dirty = true; return; }
    const c = box.getCenter(new THREE.Vector3());
    const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1);
    const dir = this.camera.position.clone().sub(this.controls.target);
    if (dir.lengthSq() < 1e-6) dir.set(1, -1.2, 0.9);
    dir.normalize();
    const dist = (r / Math.sin((this.camera.fov * Math.PI) / 360)) * pad;
    this.controls.target.copy(c);
    this.camera.position.copy(c).addScaledVector(dir, dist);
    this.camera.near = Math.max(dist / 1000, 0.01);
    this.camera.far = dist * 100;
    this.camera.updateProjectionMatrix();
    this.makeGrid(Math.max(r * 4, 40));
    const gz = box.min.z;
    this.grid.position.set(c.x, c.y, gz);
    this.controls.update();
    this.dirty = true;
  }

  setView(name: "iso" | "front" | "back" | "left" | "right" | "top" | "bottom") {
    const dirs: Record<string, [number, number, number]> = {
      iso: [1, -1.2, 0.9], front: [0, -1, 0], back: [0, 1, 0], left: [-1, 0, 0], right: [1, 0, 0], top: [0, -0.0001, 1], bottom: [0, -0.0001, -1],
    };
    const d = new THREE.Vector3(...dirs[name]).normalize();
    const dist = Math.max(this.camera.position.distanceTo(this.controls.target), 10);
    this.camera.position.copy(this.controls.target).addScaledVector(d, dist);
    this.camera.up.set(0, 0, 1);
    this.controls.update();
    this.fit();
  }

  screenshot(): string { this.renderer.render(this.scene, this.camera); return this.renderer.domElement.toDataURL("image/png"); }

  // ------------------------------------------------------------ picking
  private handlePick(ev: MouseEvent) {
    if (!this.pickCb) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(ndc, this.camera);
    const meshes: THREE.Object3D[] = [];
    this.entries.forEach((e) => { if (e.pickable && e.mesh && e.group.visible) meshes.push(e.mesh); });
    const hits = this.ray.intersectObjects(meshes, false);
    if (!hits.length) return this.pickCb(null, ev);
    const h = hits[0];
    let entry: Entry | undefined;
    this.entries.forEach((e) => { if (e.mesh === h.object) entry = e; });
    const tri = h.faceIndex ?? 0;
    const face = entry?.faces?.find((f) => tri >= f.tri_start && tri < f.tri_start + f.tri_count) ?? null;
    // report the hit point in the part's local frame (undo the assembly pose)
    const local = h.point.clone();
    if (entry) local.applyMatrix4(entry.group.matrix.clone().invert());
    this.pickCb({ key: entry?.key ?? "", face, point: local, triangle: tri }, ev);
  }
}
