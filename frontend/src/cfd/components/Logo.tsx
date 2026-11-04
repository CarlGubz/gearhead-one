import { Link } from "react-router-dom";

/**
 * Gearhead brand mark (same as Gearhead FEA): a hexagonal nut cut into six mesh triangles,
 * each tinted with one stop of the contour ramp. The product tag reads "CFD" where FEA has "FEA".
 */
export function LogoMark({ size = 28 }: { size?: number }) {
  const cx = 16, cy = 16, r = 14;
  const pts = Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 3) * i - Math.PI / 2;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  });
  const fills = ["--gh-c0", "--gh-c1", "--gh-c2", "--gh-c3", "--gh-c4", "--gh-c5"];
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      {pts.map((p, i) => {
        const q = pts[(i + 1) % 6];
        return (
          <polygon key={i} points={`${cx},${cy} ${p[0].toFixed(2)},${p[1].toFixed(2)} ${q[0].toFixed(2)},${q[1].toFixed(2)}`}
            fill={`var(${fills[i]})`} stroke="var(--gh-ink)" strokeWidth="1.4" strokeLinejoin="round" />
        );
      })}
      <circle cx={cx} cy={cy} r="3.6" fill="var(--gh-paper)" stroke="var(--gh-ink)" strokeWidth="1.4" />
    </svg>
  );
}

export function Logo({ to = "/", compact }: { to?: string; compact?: boolean }) {
  return (
    <Link to={to} className="gh-logo" aria-label="Gearhead CFD — home">
      <LogoMark />
      {!compact && (
        <span className="gh-logo__word">
          Gearhead<span className="gh-logo__tag">CFD</span>
        </span>
      )}
    </Link>
  );
}
