import React, { useEffect, useState } from "react";

/**
 * Numeric input that keeps what the user types as text and commits a parsed number on blur / Enter.
 * `onCommit(number)` is only called for valid numbers.
 */
export function Num({ label, value, onCommit, step = "any", min, max, unit, disabled, hint }) {
  const [text, setText] = useState(value === undefined || value === null ? "" : String(value));
  useEffect(() => {
    setText(value === undefined || value === null ? "" : String(value));
  }, [value]);
  const commit = () => {
    const v = parseFloat(text);
    if (Number.isFinite(v) && (min === undefined || v >= min) && (max === undefined || v <= max)) onCommit(v);
    else setText(value === undefined || value === null ? "" : String(value));
  };
  return (
    <div className="field-row">
      {label && <label>{label}{unit ? ` (${unit})` : ""}</label>}
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
      />
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}

export function Check({ label, checked, onChange, disabled }) {
  return (
    <label className="check-row">
      <input type="checkbox" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /> {label}
    </label>
  );
}

export function Select({ label, value, onChange, options, disabled }) {
  return (
    <div className="field-row">
      {label && <label>{label}</label>}
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function Vec3({ label, value, onChange, unit }) {
  const set = (i, v) => onChange(value.map((x, k) => (k === i ? v : x)));
  return (
    <div className="field-row">
      {label && <label>{label}{unit ? ` (${unit})` : ""}</label>}
      <div className="vec3">
        {["x", "y", "z"].map((a, i) => (
          <Num key={a} value={value[i]} onCommit={(v) => set(i, v)} />
        ))}
      </div>
    </div>
  );
}
