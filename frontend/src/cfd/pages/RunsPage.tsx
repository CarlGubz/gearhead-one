import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useProject } from "../ProjectContext";
import { ColorBar, FieldCanvas } from "../components/FieldCanvas";
import { ResidualChart, ValueChart } from "../components/MonitorCharts";
import { StatusChip, Toast, timeAgo } from "../components/ui";
import { COLORMAPS, type ColormapName } from "../lib/colormap";
import { decodeFields, FIELD_LABELS, formatNumber, type DecodedFields } from "../lib/fields";
import type { FieldName, LogLine, MonitorSample, Run, RunDetail } from "../types";

const ACTIVE = ["queued", "running"];

export default function RunsPage() {
  const { project, check, flush, config } = useProject();
  const { rid } = useParams();
  const nav = useNavigate();
  const [runs, setRuns] = useState<Run[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ m: string; err?: boolean; list?: string[] } | null>(null);

  const loadRuns = useCallback(() => api.runs(project.id).then(setRuns).catch(() => undefined), [project.id]);
  useEffect(() => { void loadRuns(); }, [loadRuns]);
  useEffect(() => {
    if (!runs.some((r) => ACTIVE.includes(r.status))) return;
    const t = setInterval(() => void loadRuns(), 2500);
    return () => clearInterval(t);
  }, [runs, loadRuns]);
  useEffect(() => { if (!rid && runs.length) nav(runs[0].id, { replace: true }); }, [rid, runs, nav]);

  const errors = check?.items.filter((i) => i.level === "error") ?? [];

  const start = async () => {
    setBusy(true);
    try {
      const ok = await flush();
      if (!ok) { setToast({ m: "Fix the invalid values in Setup first — the case could not be saved.", err: true }); return; }
      const r = await api.createRun(project.id, name.trim());
      setName("");
      await loadRuns();
      nav(r.id);
    } catch (e) {
      const err = e as ApiError;
      setToast({ m: err.message, err: true, list: err.details });
    } finally { setBusy(false); }
  };

  return (
    <div className="split">
      <aside className="leftlist">
        <div className="head">
          <input type="text" placeholder={`Run name (optional)`} value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && !busy && errors.length === 0 && void start()} />
          <button className="btn primary" disabled={busy || errors.length > 0} onClick={() => void start()}>{busy ? <span className="spin" /> : "▶"} Start new run</button>
          {errors.length > 0 && <div className="banner err small">The case has {errors.length} error{errors.length > 1 ? "s" : ""}: {errors[0].message} <Link to="../setup">Open Setup</Link></div>}
          <div className="muted small">Uses the current Setup values. {config.run.mode === "steady" ? "Steady-state" : "Transient"} · {config.mesh.nx}×{config.mesh.ny}{config.dimension === "3d" ? `×${config.mesh.nz}` : ""} cells · solver: {config.solver.backend}.</div>
        </div>
        {runs.length === 0 && <div className="empty" style={{ margin: 14 }}>No runs yet.</div>}
        {runs.map((r) => (
          <div key={r.id} className={`runitem ${r.id === rid ? "on" : ""}`} onClick={() => nav(r.id)}>
            <div className="row between"><b>{r.name}</b><StatusChip status={r.status} /></div>
            {ACTIVE.includes(r.status) && <div className="progress"><div style={{ width: `${r.progress * 100}%` }} /></div>}
            <div className="muted small">{timeAgo(r.created_at)} · {r.iteration.toLocaleString()} it{r.status === "completed" && r.converged ? " · converged" : ""}</div>
          </div>
        ))}
      </aside>
      <main className="mainpane">
        {rid ? <RunView key={rid} rid={rid} onChanged={loadRuns} onDeleted={() => { void loadRuns(); nav("../runs"); }} /> : <div className="empty">Start a run to see residuals, monitors and a live preview of the flow field.</div>}
      </main>
      {toast && (
        <Toast message={toast.m + (toast.list?.length ? ` — ${toast.list.join("; ")}` : "")} error={toast.err} onDone={() => setToast(null)} />
      )}
    </div>
  );
}

function useRunLive(rid: string) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [samples, setSamples] = useState<MonitorSample[]>([]);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    (async () => {
      try {
        const [r, m, l] = await Promise.all([api.run(rid), api.monitors(rid), api.logs(rid)]);
        if (closed) return;
        setRun(r); setSamples(m.samples); setLogs(l);
        if (ACTIVE.includes(r.status)) {
          const since = m.samples.length ? m.samples[m.samples.length - 1].iteration : -1;
          const after = l.length ? l[l.length - 1].id : 0;
          ws = new WebSocket(api.streamUrl(rid, since, after));
          ws.onmessage = (ev) => {
            const d = JSON.parse(ev.data);
            setRun((cur) => (cur ? { ...cur, status: d.status, progress: d.progress, iteration: d.iteration, sim_time: d.sim_time, converged: d.converged, error: d.error, summary: d.summary ?? cur.summary } : cur));
            if (d.samples?.length) setSamples((s) => { const all = s.concat(d.samples); return all.length > 8000 ? all.filter((_, i) => i % 2 === 0 || i > all.length - 500) : all; });
            if (d.logs?.length) setLogs((s) => s.concat(d.logs));
            if (!ACTIVE.includes(d.status)) void api.run(rid).then((x) => !closed && setRun(x));
          };
        }
      } catch { if (!closed) setMissing(true); }
    })();
    return () => { closed = true; ws?.close(); };
  }, [rid]);
  return { run, setRun, samples, logs, missing };
}

function RunView({ rid, onChanged, onDeleted }: { rid: string; onChanged: () => void; onDeleted: () => void }) {
  const { run, samples, logs, missing } = useRunLive(rid);
  const { config } = useProject();
  const nav = useNavigate();
  const consoleRef = useRef<HTMLDivElement>(null);
  const [fields, setFields] = useState<DecodedFields | null>(null);
  const [field, setField] = useState<FieldName>("speed");
  const [cmap, setCmap] = useState<ColormapName>("viridis");
  const [vectors, setVectors] = useState(false);

  useEffect(() => { consoleRef.current?.scrollTo({ top: 1e9 }); }, [logs.length]);
  const status = run?.status;
  useEffect(() => { if (status && !ACTIVE.includes(status)) onChanged(); }, [status]); // eslint-disable-line react-hooks/exhaustive-deps

  // live field preview
  useEffect(() => {
    if (!status) return;
    let stop = false;
    const load = () => api.fields(rid).then((p) => !stop && setFields(decodeFields(p))).catch(() => undefined);
    void load();
    if (status === "running") { const t = setInterval(load, 2500); return () => { stop = true; clearInterval(t); }; }
    return () => { stop = true; };
  }, [rid, status]);

  const range = useMemo<[number, number]>(() => (fields?.ranges[field] ?? [0, 1]), [fields, field]);
  const wall = run?.started_at ? ((run.finished_at ? new Date(run.finished_at).getTime() : Date.now()) - new Date(run.started_at).getTime()) / 1000 : 0;

  if (missing) return <div className="banner err">This run no longer exists.</div>;
  if (!run) return <span className="spin" />;
  const s = run.summary;
  const hasResults = run.status === "completed" || (run.status === "cancelled" && !!fields);
  return (
    <>
      <div className="row between wrap">
        <div className="row"><h2>{run.name}</h2><StatusChip status={run.status} />{run.status === "completed" && run.converged && <span className="chip completed">converged</span>}</div>
        <div className="row">
          {ACTIVE.includes(run.status) && <button className="btn danger" onClick={async () => { await api.cancelRun(run.id); onChanged(); }}>■ Stop</button>}
          {hasResults && <button className="btn primary" onClick={() => nav(`../results/${run.id}`)}>View results →</button>}
          {!ACTIVE.includes(run.status) && <button className="btn ghost danger" onClick={async () => { if (confirm("Delete this run and its results?")) { await api.deleteRun(run.id); onDeleted(); } }}>Delete</button>}
        </div>
      </div>

      {run.error && <div className="banner err"><b>Run failed.</b> {run.error}</div>}
      {s?.notes?.map((n, i) => <div key={i} className="banner warn small">{n}</div>)}

      <div className="card card-pad col" style={{ gap: 8 }}>
        <div className="progress"><div style={{ width: `${(run.status === "completed" ? 1 : run.progress) * 100}%` }} /></div>
        <div className="row wrap small" style={{ gap: 22 }}>
          <span><span className="muted">progress </span><b>{((run.status === "completed" ? 1 : run.progress) * 100).toFixed(0)} %</b></span>
          <span><span className="muted">iteration </span><b className="mono">{run.iteration.toLocaleString()}</b></span>
          <span><span className="muted">sim. time </span><b className="mono">{formatNumber(run.sim_time, 4)} s</b></span>
          <span><span className="muted">wall time </span><b className="mono">{formatNumber(wall, 3)} s</b></span>
          {s && <span><span className="muted">max speed </span><b className="mono">{formatNumber(s.max_speed, 4)} m/s</b></span>}
        </div>
      </div>

      <div className="twocol">
        <ResidualChart samples={samples} />
        <ValueChart samples={samples} config={run.config ?? config} />
      </div>

      <div className="twocol">
        <div className="card chartbox">
          <div className="head">
            <h3>Live field {run.status === "running" && <span className="spin" />}</h3>
            <div className="row">
              <select style={{ width: "auto" }} value={field} onChange={(e) => setField(e.target.value as FieldName)}>
                {(Object.keys(FIELD_LABELS) as FieldName[]).filter((k) => (k !== "T" || fields?.energy) && (k !== "w" || fields?.dim === 3)).map((k) => <option key={k} value={k}>{FIELD_LABELS[k].label}</option>)}
              </select>
              <select style={{ width: "auto" }} value={cmap} onChange={(e) => setCmap(e.target.value as ColormapName)}>{COLORMAPS.map((c) => <option key={c}>{c}</option>)}</select>
              <label className="toggle small"><input type="checkbox" checked={vectors} onChange={(e) => setVectors(e.target.checked)} /><span className="sw" />vectors</label>
            </div>
          </div>
          {fields ? (
            <div className="col" style={{ gap: 6 }}>
              <FieldCanvas fields={fields} field={field} cmap={cmap} range={range} smooth vectors={vectors} streams={false} maxHeight={320} />
              <ColorBar cmap={cmap} range={range} field={field} />
              <div className="muted small">t = {formatNumber(fields.time, 4)} s{fields.iteration ? ` · iteration ${fields.iteration}` : ""}</div>
            </div>
          ) : <div className="muted small" style={{ padding: 30, textAlign: "center" }}>The first snapshot appears after a moment…</div>}
        </div>
        <div className="col">
          <div className="card chartbox"><div className="head"><h3>Console</h3></div>
            <div className="console" ref={consoleRef}>
              {logs.length === 0 && <div className="muted">Waiting for the solver to start…</div>}
              {logs.map((l) => <div key={l.id} className={l.level}>[{new Date(l.ts).toLocaleTimeString()}] {l.message}</div>)}
            </div>
          </div>
          {s && (
            <div className="card card-pad">
              <h3 style={{ marginBottom: 8 }}>Summary</h3>
              <dl className="kv">
                <dt>Iterations</dt><dd>{s.iterations.toLocaleString()}</dd>
                <dt>Reference Reynolds</dt><dd>{formatNumber(s.reynolds_ref, 4)}</dd>
                <dt>Pressure range</dt><dd>{formatNumber(s.p_min, 4)} … {formatNumber(s.p_max, 4)} Pa</dd>
                {s.pressure_drop !== undefined && <><dt>Inlet–outlet Δp</dt><dd>{formatNumber(s.pressure_drop, 4)} Pa</dd></>}
                {s.T_min !== undefined && <><dt>Temperature range</dt><dd>{formatNumber(s.T_min!, 5)} … {formatNumber(s.T_max!, 5)} K</dd></>}
                <dt>Mass imbalance</dt><dd>{formatNumber(s.mass_imbalance, 3)}</dd>
              </dl>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
