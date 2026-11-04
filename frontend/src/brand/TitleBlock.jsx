import React from "react";
import "./brand.css";

/** Engineering-drawing title block. items: [{ k, v, wide }] */
export default function TitleBlock({ items }) {
  return (
    <div className="gh-titleblock" role="group" aria-label="Sheet information">
      {items.map((it) => (
        <div key={it.k} className={`gh-titleblock__cell${it.wide ? " gh-titleblock__cell--wide" : ""}`}>
          <span className="gh-titleblock__k">{it.k}</span>
          <span className="gh-titleblock__v">{it.v}</span>
        </div>
      ))}
    </div>
  );
}
