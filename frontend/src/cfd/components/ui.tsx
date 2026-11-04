import { useEffect, useRef, useState, type ReactNode } from "react";

/** Numeric input that accepts scientific notation and commits on blur / Enter. */
export function NumberInput(props: {
  value: number | null;
  onChange: (v: number) => void;
  unit?: string;
  min?: number;
  max?: number;
  integer?: boolean;
  placeholder?: string;
  allowEmpty?: boolean;
  onEmpty?: () => void;
}) {
  const { value, onChange, unit, min, max, integer, placeholder, allowEmpty, onEmpty } = props;
  const fmt = (v: number | null) => (v === null || v === undefined ? "" : String(Number(v.toPrecision(10))));
  const [text, setText] = useState(fmt(value));
  const [bad, setBad] = useState(false);
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) { setText(fmt(value)); setBad(false); }
  }, [value]);
  const commit = () => {
    const t = text.trim();
    if (t === "" && allowEmpty) { onEmpty?.(); setBad(false); return; }
    let n = Number(t);
    if (t === "" || !Number.isFinite(n)) { setBad(true); return; }
    if (integer) n = Math.round(n);
    if (min !== undefined && n < min) n = min;
    if (max !== undefined && n > max) n = max;
    setBad(false);
    setText(fmt(n));
    if (n !== value) onChange(n);
  };
  return (
    <div className="numwrap">
      <input
        type="text"
        inputMode="decimal"
        className={bad ? "invalid" : ""}
        value={text}
        placeholder={placeholder}
        onFocus={() => (focused.current = true)}
        onBlur={() => { focused.current = false; commit(); }}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
      />
      {unit && <span className="unit">{unit}</span>}
    </div>
  );
}

export function Field(props: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <label>{props.label}</label>
      {props.children}
      {props.hint && <div className="hint">{props.hint}</div>}
    </div>
  );
}

export function Toggle(props: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode; hint?: ReactNode }) {
  return (
    <div className="field">
      <label className="toggle">
        <input type="checkbox" checked={props.checked} onChange={(e) => props.onChange(e.target.checked)} />
        <span className="sw" />
        <span style={{ fontWeight: 550 }}>{props.label}</span>
      </label>
      {props.hint && <div className="hint">{props.hint}</div>}
    </div>
  );
}

export function Segmented<T extends string>(props: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {props.options.map((o) => (
        <button key={o.value} className={o.value === props.value ? "on" : ""} onClick={() => props.onChange(o.value)} type="button">
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function StatusChip({ status }: { status: string }) {
  return (
    <span className={`chip ${status}`}>
      <span className="dot" />
      {status}
    </span>
  );
}

export function Toast({ message, error, onDone }: { message: string; error?: boolean; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, error ? 6000 : 2800);
    return () => clearTimeout(t);
  }, [message, error, onDone]);
  return <div className={`toast ${error ? "err" : ""}`}>{message}</div>;
}

export function Modal(props: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && props.onClose()}>
      <div className="card modal">
        <h2>{props.title}</h2>
        {props.children}
      </div>
    </div>
  );
}

export function timeAgo(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
