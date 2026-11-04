import { ReactNode, useEffect, useState } from "react";
import type { Json } from "../types";

export function Section({ title, actions, children, pad = true }: { title: string; actions?: ReactNode; children?: ReactNode; pad?: boolean }) {
  return (
    <section className="section">
      <header>
        <span className="grow">{title}</span>
        {actions}
      </header>
      {pad ? <div className="body">{children}</div> : children}
    </section>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

/** Numeric input that also accepts "=expression" (resolved on the server against part parameters). */
export function NumField({ value, onChange, step, allowExpr = true, placeholder }: {
  value: Json; onChange: (v: Json) => void; step?: number; allowExpr?: boolean; placeholder?: string;
}) {
  const [text, setText] = useState(value === null || value === undefined ? "" : String(value));
  useEffect(() => setText(value === null || value === undefined ? "" : String(value)), [value]);
  const commit = () => {
    const t = text.trim();
    if (t === "") return onChange(null);
    if (allowExpr && t.startsWith("=")) return onChange(t);
    const n = Number(t);
    if (Number.isFinite(n)) onChange(n);
    else setText(value === null || value === undefined ? "" : String(value));
  };
  const isExpr = typeof value === "string" && value.startsWith("=");
  return (
    <input
      type="text"
      inputMode="decimal"
      className={isExpr ? "expr" : ""}
      value={text}
      placeholder={placeholder}
      step={step}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          const n = Number(text);
          if (Number.isFinite(n)) {
            e.preventDefault();
            const d = (e.key === "ArrowUp" ? 1 : -1) * (step ?? 1) * (e.shiftKey ? 10 : 1);
            const nv = Math.round((n + d) * 1e6) / 1e6;
            setText(String(nv));
            onChange(nv);
          }
        }
      }}
    />
  );
}

export function VecField({ value, onChange, n }: { value: Json; onChange: (v: Json) => void; n: 2 | 3 }) {
  const v: Json[] = Array.isArray(value) ? value : Array(n).fill(0);
  return (
    <div className={`vec v${n}`}>
      {Array.from({ length: n }).map((_, i) => (
        <NumField key={i} value={v[i] ?? 0} onChange={(x) => { const c = [...v]; while (c.length < n) c.push(0); c[i] = x ?? 0; onChange(c); }} />
      ))}
    </div>
  );
}

export function JsonField({ value, onChange }: { value: Json; onChange: (v: Json) => void }) {
  const [text, setText] = useState(JSON.stringify(value ?? []));
  const [bad, setBad] = useState(false);
  useEffect(() => setText(JSON.stringify(value ?? [])), [value]);
  return (
    <textarea
      value={text}
      style={bad ? { borderColor: "var(--danger)" } : undefined}
      onChange={(e) => {
        setText(e.target.value);
        try { onChange(JSON.parse(e.target.value)); setBad(false); } catch { setBad(true); }
      }}
    />
  );
}

export function Select({ value, onChange, options }: { value: Json; onChange: (v: string) => void; options: { value: Json; label: string }[] }) {
  return (
    <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => (
        <option key={String(o.value)} value={String(o.value)}>{o.label}</option>
      ))}
    </select>
  );
}

export function Toast({ msg, kind, onClose }: { msg: string; kind: "err" | "ok"; onClose: () => void }) {
  useEffect(() => {
    const t = setTimeout(onClose, kind === "err" ? 7000 : 2600);
    return () => clearTimeout(t);
  }, [msg, kind, onClose]);
  return <div className={`toast ${kind === "ok" ? "ok" : ""}`} onClick={onClose}>{msg}</div>;
}

export function Spinner({ label }: { label?: string }) {
  return <span className="muted small">⏳ {label ?? "working…"}</span>;
}

export const fmt = (n: number | null | undefined, d = 2) =>
  n === null || n === undefined || !Number.isFinite(n) ? "–" : Math.abs(n) >= 1e5 || (Math.abs(n) < 1e-3 && n !== 0) ? n.toExponential(2) : n.toFixed(d);

export const fmtTime = (s: number) => {
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return m > 0 ? `${m} min ${sec} s` : `${sec} s`;
};

export function download(filename: string, text: string, mime = "text/plain") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: mime }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
