import { useMemo, useState } from "react";
import { LineChart, seriesColor, type Series } from "./LineChart";
import type { CaseConfig, MonitorSample } from "../types";

export function ResidualChart({ samples }: { samples: MonitorSample[] }) {
  const series = useMemo<Series[]>(() => {
    const keys = new Set<string>();
    samples.forEach((s) => Object.keys(s.residuals).forEach((k) => keys.add(k)));
    const labels: Record<string, string> = { u: "u-velocity", v: "v-velocity", w: "w-velocity", p: "pressure", T: "temperature" };
    return [...keys].map((k, i) => ({
      name: labels[k] ?? k,
      color: seriesColor(i),
      x: samples.filter((s) => k in s.residuals).map((s) => s.iteration),
      y: samples.filter((s) => k in s.residuals).map((s) => s.residuals[k]),
    }));
  }, [samples]);
  return (
    <div className="card chartbox">
      <div className="head"><h3>Residuals</h3><span className="muted small">relative change per flow-through time</span></div>
      {samples.length === 0 ? <div className="muted small" style={{ padding: 30, textAlign: "center" }}>Waiting for the first iterations…</div> : <LineChart series={series} logY xLabel="iteration" />}
    </div>
  );
}

function label(key: string, config: CaseConfig): { group: string; text: string } {
  const [id, q] = key.split(".");
  const probe = config.probes.find((p) => p.id === id);
  if (probe) return { group: "Probes", text: `${probe.name} · ${q}` };
  const ob = config.obstacles.find((o) => o.id === id);
  const loadNames: Record<string, string> = { Fx: "force x", Fy: "force y", Fz: "force z", Q: "heat transfer Q" };
  if (ob) return { group: "Obstacle loads", text: `${ob.name} · ${loadNames[q] ?? q}` };
  const names: Record<string, string> = { max_speed: "Maximum speed", dt: "Time step", mass_imbalance: "Mass imbalance" };
  return { group: "Global", text: names[key] ?? key };
}

export function ValueChart({ samples, config }: { samples: MonitorSample[]; config: CaseConfig }) {
  const keys = useMemo(() => {
    const k = new Set<string>();
    samples.slice(-3).forEach((s) => Object.keys(s.values).forEach((x) => k.add(x)));
    return [...k];
  }, [samples]);
  const [sel, setSel] = useState<string[]>([]);
  const active = sel.filter((k) => keys.includes(k));
  const effective = active.length ? active : keys.filter((k) => k.endsWith(".Fx") || k.endsWith(".u")).slice(0, 2).concat(keys.length ? [] : []);
  const useKeys = effective.length ? effective : keys.filter((k) => k === "max_speed");
  const [xmode, setXmode] = useState<"time" | "iteration">("time");
  const series = useMemo<Series[]>(
    () => useKeys.map((k, i) => ({
      name: label(k, config).text,
      color: seriesColor(i),
      x: samples.filter((s) => k in s.values).map((s) => (xmode === "time" ? s.time : s.iteration)),
      y: samples.filter((s) => k in s.values).map((s) => s.values[k]),
    })),
    [samples, useKeys.join("|"), xmode, config], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const groups = new Map<string, string[]>();
  keys.forEach((k) => { const g = label(k, config).group; groups.set(g, [...(groups.get(g) ?? []), k]); });
  return (
    <div className="card chartbox">
      <div className="head">
        <h3>Monitors</h3>
        <div className="row">
          <select style={{ width: "auto" }} value="" onChange={(e) => e.target.value && setSel((s) => (s.includes(e.target.value) ? s : [...s, e.target.value]))}>
            <option value="">＋ add quantity…</option>
            {[...groups].map(([g, ks]) => <optgroup key={g} label={g}>{ks.map((k) => <option key={k} value={k}>{label(k, config).text}</option>)}</optgroup>)}
          </select>
          <select style={{ width: "auto" }} value={xmode} onChange={(e) => setXmode(e.target.value as "time" | "iteration")}><option value="time">vs time</option><option value="iteration">vs iteration</option></select>
          {sel.length > 0 && <button className="btn sm ghost" onClick={() => setSel([])}>Reset</button>}
        </div>
      </div>
      {samples.length === 0 || series.length === 0 ? <div className="muted small" style={{ padding: 30, textAlign: "center" }}>No monitor data yet.</div> : <LineChart series={series} xLabel={xmode === "time" ? "time [s]" : "iteration"} />}
    </div>
  );
}
