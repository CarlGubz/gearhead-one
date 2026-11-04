import { useState } from "react";
import type { Feature, Json, ParamDef, Sketch, Tool } from "../types";
import { Field, NumField, Select, VecField } from "./ui";

export interface FormCtx {
  sketches?: Sketch[];
  features?: Feature[];
  tools?: Tool[];
  parts?: { id: number; name: string }[];
  /** Persistent selectors ("ref:…") for the face / nearest edge picked in the 3D view. */
  picked?: { face?: string; edge?: string };
  onUsePicked?: (name: string) => void;
}

/** Short human label for a selector; persistent refs are JSON and unreadable raw. */
export function describeSelector(v: string): string {
  if (!v?.startsWith("ref:")) return v;
  try {
    const sigs = JSON.parse(v.slice(4));
    const list = Array.isArray(sigs) ? sigs : [sigs];
    const one = (s: Json) => {
      const from = Array.isArray(s.o) && typeof s.o[0] === "string" ? ` of ${s.o[0]}` : "";
      const r = s.r ? ` r${+s.r.toFixed(2)}` : "";
      return `${String(s.g ?? "").toLowerCase()} ${s.k}${r}${from}`;
    };
    return list.length === 1 ? one(list[0]) : `${list.length} ${list[0].k}s (${list.map(one).join(", ")})`;
  } catch { return "reference"; }
}

/** Selector field: shows persistent references as a readable pill, raw text otherwise. */
export function SelectorInput({ value, onChange, picked, pickLabel = "use pick" }: {
  value: string; onChange: (v: string) => void; picked?: string; pickLabel?: string;
}) {
  const [raw, setRaw] = useState(false);
  const isRef = typeof value === "string" && value.startsWith("ref:");
  return (
    <div className="row">
      {isRef && !raw ? (
        <span className="pill grow" title="Persistent reference: survives upstream edits that renumber faces / edges" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>📌 {describeSelector(value)}</span>
      ) : (
        <input type="text" className="mono" value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
      )}
      {isRef && <button className="btn ghost sm" title={raw ? "Show summary" : "Edit raw selector"} onClick={() => setRaw(!raw)}>{raw ? "◉" : "✎"}</button>}
      {picked && <button className="btn sm" title="Use the face / edge picked in the viewport" onClick={() => { onChange(picked); setRaw(false); }}>{pickLabel}</button>}
    </div>
  );
}

/** Renders any backend parameter schema (features, CAM operations) as a form. */
export function ParamForm({ defs, values, onChange, ctx = {} }: {
  defs: ParamDef[]; values: Record<string, Json>; onChange: (name: string, v: Json) => void; ctx?: FormCtx;
}) {
  return (
    <div className="col">
      {defs.map((d) => {
        const v = values[d.name] ?? d.default;
        const set = (x: Json) => onChange(d.name, x);
        let input;
        switch (d.type) {
          case "bool":
            return (
              <label key={d.name} className="check">
                <input type="checkbox" checked={!!v} onChange={(e) => set(e.target.checked)} />
                <span>{d.label}</span>
              </label>
            );
          case "select":
            input = <Select value={v} onChange={(x) => set(isNaN(Number(x)) || x === "" ? x : (d.options ?? []).some((o) => typeof o === "number") ? Number(x) : x)} options={(d.options ?? []).map((o) => ({ value: o, label: String(o) }))} />;
            break;
          case "vec2": input = <VecField n={2} value={v} onChange={set} />; break;
          case "vec3": input = <VecField n={3} value={v} onChange={set} />; break;
          case "sketch":
            input = (
              <Select
                value={v ?? ""}
                onChange={(x) => set(x === "" ? null : Number(x))}
                options={[{ value: "", label: "— choose sketch —" }, ...(ctx.sketches ?? []).map((s) => ({ value: s.id, label: `${s.name} (${s.plane})` }))]}
              />
            );
            break;
          case "sketchlist": {
            const sel: number[] = Array.isArray(v) ? v : [];
            input = (
              <div className="col" style={{ gap: 3 }}>
                {(ctx.sketches ?? []).map((s) => (
                  <label key={s.id} className="check">
                    <input type="checkbox" checked={sel.includes(s.id)} onChange={(e) => set(e.target.checked ? [...sel, s.id] : sel.filter((i) => i !== s.id))} />
                    <span>{s.name}</span>
                  </label>
                ))}
              </div>
            );
            break;
          }
          case "feature":
            input = (
              <Select
                value={v ?? ""}
                onChange={(x) => set(x === "" ? null : x)}
                options={[{ value: "", label: "— previous —" }, ...(ctx.features ?? []).map((f) => ({ value: f.id, label: f.name }))]}
              />
            );
            break;
          case "part":
            input = <Select value={v ?? ""} onChange={(x) => set(Number(x))} options={(ctx.parts ?? []).map((p) => ({ value: p.id, label: p.name }))} />;
            break;
          case "tool":
            input = <Select value={v ?? ""} onChange={(x) => set(Number(x))} options={(ctx.tools ?? []).map((t) => ({ value: t.id, label: `T${t.id} ${t.name}` }))} />;
            break;
          case "selector": {
            const isEdge = /edge/i.test(d.name);
            const pick = isEdge ? ctx.picked?.edge : ctx.picked?.face;
            input = <SelectorInput value={v} onChange={set} picked={pick} pickLabel={isEdge ? "use edge" : "use face"} />;
            break;
          }
          case "points":
          case "points2":
            input = <input type="text" className="mono" value={v ?? ""} onChange={(e) => set(e.target.value)} placeholder="x,y,z; x,y,z" />;
            break;
          case "string":
            input = <input type="text" value={v ?? ""} onChange={(e) => set(e.target.value)} />;
            break;
          default:
            input = <NumField value={v} onChange={(x) => set(x ?? d.default)} />;
        }
        return <Field key={d.name} label={d.label}>{input}</Field>;
      })}
    </div>
  );
}
