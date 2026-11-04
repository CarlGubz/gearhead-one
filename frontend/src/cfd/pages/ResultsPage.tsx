import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type Slice } from "../api";
import { useProject } from "../ProjectContext";
import { ColorBar, FieldCanvas } from "../components/FieldCanvas";
import { LineChart, seriesColor } from "../components/LineChart";
import { ResidualChart, ValueChart } from "../components/MonitorCharts";
import { NumberInput, StatusChip, Toggle } from "../components/ui";
import { COLORMAPS, type ColormapName } from "../lib/colormap";
import { decodeFields, FIELD_LABELS, formatNumber, type DecodedFields } from "../lib/fields";
import type { FieldName, MonitorSample, ProbeLine, Run, RunDetail } from "../types";

export default function ResultsPage() {
  const { project } = useProject();
  const { rid } = useParams();
  const nav = useNavigate();
  const [runs, setRuns] = useState<Run[] | null>(null);

  useEffect(() => { api.runs(project.id).then(setRuns).catch(() => setRuns([])); }, [project.id]);
  const usable = useMemo(() => (runs ?? []).filter((r) => r.status === "completed" || r.status === "cancelled" || r.status === "running"), [runs]);
  useEffect(() => { if (!rid && usable.length) nav(usable.find((r) => r.status === "completed")?.id ?? usable[0].id, { replace: true }); }, [rid, usable, nav]);

  if (runs === null) return <div style={{ padding: 24 }}><span className="spin" /></div>;
  if (!rid) return <div className="container"><div className="empty">No results yet. Start a run in <b>Run &amp; monitor</b>.</div></div>;
  return <Viewer key={rid} rid={rid} runs={usable} onPick={(id) => nav(`../results/${id}`)} />;
}

function Viewer({ rid, runs, onPick }: { rid: string; runs: Run[]; onPick: (id: string) => void }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [samples, setSamples] = useState<MonitorSample[]>([]);
  const [times, setTimes] = useState<number[]>([]);
  const [frame, setFrame] = useState(-1);
  const [fields, setFields] = useState<DecodedFields | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cache = useRef(new Map<number, DecodedFields>());

  const [field, setField] = useState<FieldName>("speed");
  const [cmap, setCmap] = useState<ColormapName>("viridis");
  const [smooth, setSmooth] = useState(true);
  const [vectors, setVectors] = useState(false);
  const [streams, setStreams] = useState(false);
  const [lock, setLock] = useState<[number, number] | null>(null);
  const [probeMode, setProbeMode] = useState(false);
  const [line, setLine] = useState<{ p0: [number, number]; p1: [number, number] } | null>(null);
  const [profile, setProfile] = useState<ProbeLine | null>(null);
  const [profField, setProfField] = useState<FieldName>("speed");
  const [playing, setPlaying] = useState(false);
  const [fps, setFps] = useState(8);
  const [slice, setSlice] = useState<Slice | null>(null);
  const [sliceCount, setSliceCount] = useState<[number, number, number] | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    api.run(rid).then(setRun).catch((e) => setError(e.message));
    api.monitors(rid).then((m) => setSamples(m.samples)).catch(() => undefined);
    api.frames(rid).then((f) => setTimes(f.times)).catch(() => undefined);
  }, [rid]);

  const nFrames = times.length || 1;
  const frameIdx = frame < 0 ? nFrames - 1 : Math.min(frame, nFrames - 1);

  const loadFrame = useCallback(async (k: number) => {
    const key = slice ? k * 10_000 + slice.axis * 1000 + slice.index + 1 : k;
    const hit = cache.current.get(key);
    if (hit) return hit;
    const d = decodeFields(await api.fields(rid, k, slice));
    cache.current.set(key, d);
    if (cache.current.size > 40) cache.current.delete(cache.current.keys().next().value as number);
    return d;
  }, [rid, slice]);

  // 3-D: remember the slice the server picked by default and the number of layers per axis
  useEffect(() => {
    if (!fields?.plane || !run) return;
    if (!slice) setSlice({ axis: fields.plane.axis, index: fields.plane.index });
    const m = run.config.mesh;
    setSliceCount([m.nx, m.ny, m.nz]);
  }, [fields, run, slice]);

  useEffect(() => {
    let stop = false;
    const t = setTimeout(() => {
      loadFrame(frameIdx).then((d) => !stop && setFields(d)).catch((e) => !stop && setError(e.message));
    }, 30);
    return () => { stop = true; clearTimeout(t); };
  }, [frameIdx, loadFrame, nFrames]);

  useEffect(() => {
    if (!playing || nFrames < 2) return;
    const t = setInterval(() => setFrame((f) => { const cur = f < 0 ? nFrames - 1 : f; return cur + 1 >= nFrames ? 0 : cur + 1; }), 1000 / fps);
    return () => clearInterval(t);
  }, [playing, fps, nFrames]);
  useEffect(() => {
    if (!playing) return;
    for (let k = 1; k <= 3; k++) void loadFrame((frameIdx + k) % nFrames).catch(() => undefined);
  }, [playing, frameIdx, nFrames, loadFrame]);

  useEffect(() => {
    if (!line) { setProfile(null); return; }
    api.probeLine(rid, line.p0, line.p1, frameIdx, 160, slice).then(setProfile).catch(() => undefined);
  }, [line, frameIdx, rid, slice]);

  const range: [number, number] = lock ?? (fields?.ranges[field] ?? [0, 1]);
  const energy = fields?.energy ?? false;
  const is3d = fields?.dim === 3;
  const available = (k: FieldName) => (k !== "T" || energy) && (k !== "w" || is3d);
  const perDepth = is3d ? "" : "/m";
  const cfg = run?.config;

  if (error) return <div className="container"><div className="banner err">{error}</div></div>;
  const s = run?.summary;
  const download = (u: string) => { const a = document.createElement("a"); a.href = u; a.click(); };

  return (
    <div className="results">
      <aside className="controls">
        <div className="field"><label>Run</label>
          <select value={rid} onChange={(e) => onPick(e.target.value)}>{runs.map((r) => <option key={r.id} value={r.id}>{r.name} — {r.status}</option>)}</select></div>
        {run && <div className="row"><StatusChip status={run.status} /><span className="muted small">{run.iteration.toLocaleString()} iterations · t = {formatNumber(run.sim_time, 4)} s</span></div>}

        {is3d && slice && sliceCount && (
          <div className="subcard col" style={{ gap: 8 }}>
            <div className="field"><label>Slice plane</label>
              <select value={slice.axis} onChange={(e) => { const axis = Number(e.target.value) as 0 | 1 | 2; setLine(null); setSlice({ axis, index: Math.floor(sliceCount[axis] / 2) }); }}>
                <option value={2}>x–y (normal z)</option><option value={1}>x–z (normal y)</option><option value={0}>y–z (normal x)</option>
              </select></div>
            <div className="field"><label>Position: {fields?.plane ? `${fields.plane.normal} = ${formatNumber(fields.plane.position, 4)} m` : ""} (layer {slice.index + 1}/{sliceCount[slice.axis]})</label>
              <input type="range" min={0} max={sliceCount[slice.axis] - 1} value={slice.index} onChange={(e) => setSlice({ axis: slice.axis, index: Number(e.target.value) })} /></div>
          </div>
        )}
        <div className="field"><label>Contour field</label>
          <select value={field} onChange={(e) => { setField(e.target.value as FieldName); setLock(null); }}>
            {(Object.keys(FIELD_LABELS) as FieldName[]).filter(available).map((k) => <option key={k} value={k}>{FIELD_LABELS[k].label} [{FIELD_LABELS[k].unit}]</option>)}
          </select></div>
        <div className="field"><label>Colour map</label>
          <select value={cmap} onChange={(e) => setCmap(e.target.value as ColormapName)}>{COLORMAPS.map((c) => <option key={c}>{c}</option>)}</select></div>
        <div className="col" style={{ gap: 8 }}>
          <Toggle checked={lock !== null} onChange={(v) => setLock(v && fields ? [...(fields.ranges[field] ?? [0, 1])] as [number, number] : null)} label="Fix colour range" hint="Keeps the scale constant across animation frames and slices." />
          {lock && (
            <div className="grid2">
              <NumberInput value={lock[0]} onChange={(v) => setLock([v, lock[1]])} />
              <NumberInput value={lock[1]} onChange={(v) => setLock([lock[0], v])} />
            </div>
          )}
        </div>
        <Toggle checked={smooth} onChange={setSmooth} label="Smooth shading" />
        <Toggle checked={vectors} onChange={setVectors} label="Velocity vectors" />
        <Toggle checked={streams} onChange={setStreams} label="Streamlines" />

        <div className="subcard col" style={{ gap: 8 }}>
          <Toggle checked={probeMode} onChange={(v) => { setProbeMode(v); if (!v) setLine(null); }} label="Line probe" hint="Click and drag across the field to plot a profile." />
          {line && <button className="btn sm" onClick={() => setLine(null)}>Clear line</button>}
        </div>

        <div className="col" style={{ gap: 8 }}>
          <label className="muted small" style={{ fontWeight: 600 }}>EXPORT</label>
          <button className="btn sm" disabled={!fields} onClick={() => download(api.exportUrl(rid, "vtu", frameIdx))}>⬇ ParaView (.vtu)</button>
          <button className="btn sm" disabled={!fields} onClick={() => download(api.exportUrl(rid, "csv", frameIdx))}>⬇ Field data (.csv)</button>
          <button className="btn sm" onClick={() => download(api.exportUrl(rid, "monitors"))}>⬇ Monitors (.csv)</button>
          <button className="btn sm" disabled={!fields} onClick={() => canvasRef.current?.toBlob((b) => { if (b) { const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = `${run?.name ?? "result"}_${field}.png`; a.click(); } })}>⬇ Image (.png)</button>
        </div>
      </aside>

      <section className="viewer">
        {!fields ? <span className="spin" /> : (
          <>
            <div className="row between wrap">
              <h2>{FIELD_LABELS[field].label}{fields.plane ? <span className="muted small"> · {fields.axes.join("–")} slice at {fields.plane.normal} = {formatNumber(fields.plane.position, 4)} m</span> : null}</h2>
              <span className="muted small mono">t = {formatNumber(fields.time, 5)} s{nFrames > 1 ? ` · frame ${frameIdx + 1}/${nFrames}` : ""}</span>
            </div>
            <FieldCanvas fields={fields} field={field} cmap={cmap} range={range} smooth={smooth} vectors={vectors} streams={streams}
              line={line} probeMode={probeMode} onLine={(p0, p1) => setLine({ p0, p1 })} canvasRef={canvasRef} maxHeight={560} />
            <ColorBar cmap={cmap} range={range} field={field} />
            {nFrames > 1 && (
              <div className="card card-pad player">
                <button className="btn" onClick={() => setPlaying((p) => !p)}>{playing ? "❚❚ Pause" : "▶ Play"}</button>
                <input type="range" min={0} max={nFrames - 1} value={frameIdx} onChange={(e) => { setPlaying(false); setFrame(Number(e.target.value)); }} />
                <span className="mono small" style={{ minWidth: 90, textAlign: "right" }}>{formatNumber(times[frameIdx] ?? fields.time, 4)} s</span>
                <select style={{ width: "auto" }} value={fps} onChange={(e) => setFps(Number(e.target.value))}>{[2, 4, 8, 15, 24].map((f) => <option key={f} value={f}>{f} fps</option>)}</select>
              </div>
            )}

            {profile && (
              <div className="card chartbox">
                <div className="head"><h3>Profile along line</h3>
                  <div className="row"><select style={{ width: "auto" }} value={profField} onChange={(e) => setProfField(e.target.value as FieldName)}>
                    {(Object.keys(FIELD_LABELS) as FieldName[]).filter(available).map((k) => <option key={k} value={k}>{FIELD_LABELS[k].label}</option>)}</select>
                    <button className="btn sm" onClick={() => {
                      const rows = [`distance,${fields.axes.join(",")},` + Object.keys(profile.values).join(",")];
                      profile.distance.forEach((d, i) => rows.push([d, profile.x[i], profile.y[i], ...Object.values(profile.values).map((v) => v![i])].join(",")));
                      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" })); a.download = "profile.csv"; a.click();
                    }}>⬇ CSV</button></div></div>
                <LineChart series={[{ name: `${FIELD_LABELS[profField].label} [${FIELD_LABELS[profField].unit}]`, color: seriesColor(0), x: profile.distance, y: profile.values[profField] ?? [] }]} xLabel="distance along line [m]" />
              </div>
            )}

            <div className="twocol">
              <ValueChart samples={samples} config={cfg ?? emptyCfg} />
              <ResidualChart samples={samples} />
            </div>

            {s && (
              <div className="twocol">
                <div className="card card-pad">
                  <h3 style={{ marginBottom: 8 }}>Run summary</h3>
                  <dl className="kv">
                    {s.backend && <><dt>Solver</dt><dd>{s.backend}{s.requested_backend && s.requested_backend !== s.backend ? ` (requested ${s.requested_backend})` : ""}</dd></>}
                    <dt>Cells (fluid)</dt><dd>{s.cells.toLocaleString()} ({s.fluid_cells.toLocaleString()})</dd>
                    <dt>Iterations</dt><dd>{s.iterations.toLocaleString()}</dd>
                    <dt>Simulated time</dt><dd>{formatNumber(s.sim_time, 4)} s</dd>
                    <dt>Wall time</dt><dd>{formatNumber(s.wall_time_s, 3)} s</dd>
                    <dt>Converged</dt><dd>{s.converged ? "yes" : "no"}</dd>
                    <dt>Reference Reynolds</dt><dd>{formatNumber(s.reynolds_ref, 4)}</dd>
                    <dt>Max speed</dt><dd>{formatNumber(s.max_speed, 4)} m/s</dd>
                    <dt>Pressure range</dt><dd>{formatNumber(s.p_min, 4)} … {formatNumber(s.p_max, 4)} Pa</dd>
                    {s.pressure_drop !== undefined && <><dt>Inlet–outlet Δp</dt><dd>{formatNumber(s.pressure_drop, 4)} Pa</dd></>}
                    {s.T_min !== undefined && <><dt>Temperature range</dt><dd>{formatNumber(s.T_min!, 5)} … {formatNumber(s.T_max!, 5)} K</dd></>}
                    <dt>Mass imbalance</dt><dd>{formatNumber(s.mass_imbalance, 3)}</dd>
                  </dl>
                </div>
                {s.obstacles && Object.keys(s.obstacles).length > 0 && (
                  <div className="card card-pad">
                    <h3 style={{ marginBottom: 8 }}>Loads on obstacles {!is3d && <span className="muted small">(per metre depth)</span>}</h3>
                    <table className="t">
                      <thead><tr><th>Obstacle</th><th>Fx [N{perDepth}]</th><th>Fy [N{perDepth}]</th>{is3d && <th>Fz [N]</th>}{energy && <th>Q [W{perDepth}]</th>}</tr></thead>
                      <tbody>
                        {Object.entries(s.obstacles).map(([id, o]) => (
                          <tr key={id}><td>{cfg?.obstacles.find((x) => x.id === id)?.name ?? id}</td><td>{formatNumber(o.Fx, 4)}</td><td>{formatNumber(o.Fy, 4)}</td>{is3d && <td>{formatNumber(o.Fz ?? 0, 4)}</td>}{energy && <td>{formatNumber(o.Q, 4)}</td>}</tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="muted small" style={{ marginTop: 8 }}>Pressure + viscous force on the staircase surface; Q &gt; 0 means the solid heats the fluid.</p>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

const emptyCfg = { probes: [], obstacles: [] } as unknown as import("../types").CaseConfig;
