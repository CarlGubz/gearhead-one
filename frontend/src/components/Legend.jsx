import React from "react";

export default function Legend({ label, min, max, unit }) {
  if (min === undefined || max === undefined) return null;
  return (
    <div className="legend">
      <div className="legend-title">{label}</div>
      <div className="legend-bar" />
      <div className="legend-labels">
        <span>{min}</span>
        <span>{max} {unit}</span>
      </div>
    </div>
  );
}
