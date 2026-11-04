/*
  Reusable UI primitives for the public site, account and billing screens.
  Styles live in ./ui.css. Import from "../ui" — one barrel, no deep paths.
*/
import React, { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import "./ui.css";

const cx = (...parts) => parts.filter(Boolean).join(" ");

/* ---------------------------------------------------------------- Spinner */
export function Spinner({ label = "Loading" }) {
  return <span className="gh-spinner" role="status" aria-label={label} />;
}

/* ----------------------------------------------------------------- Button */
/**
 * variant: primary | secondary | ghost | danger     size: sm | md | lg
 * Pass `to` to render a router <Link>, `href` for a plain anchor.
 */
export function Button({
  to,
  href,
  variant = "primary",
  size = "md",
  block = false,
  loading = false,
  className,
  children,
  disabled,
  ...rest
}) {
  const classes = cx(
    "gh-btn",
    variant !== "primary" && `gh-btn--${variant}`,
    size !== "md" && `gh-btn--${size}`,
    block && "gh-btn--block",
    className
  );
  if (to) {
    return (
      <Link to={to} className={classes} {...rest}>
        {children}
      </Link>
    );
  }
  if (href) {
    return (
      <a href={href} className={classes} {...rest}>
        {children}
      </a>
    );
  }
  return (
    <button
      type="button"
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <Spinner />}
      {children}
    </button>
  );
}

/* -------------------------------------------------------------- TextField */
export function TextField({ label, hint, error, type = "text", className, ...input }) {
  const id = useId();
  const [shown, setShown] = useState(false);
  const isPassword = type === "password";
  const describedBy = error ? `${id}-err` : hint ? `${id}-hint` : undefined;
  return (
    <div className={cx("gh-field", error && "gh-field--error", className)}>
      <label className="gh-field__label" htmlFor={id}>
        {label}
      </label>
      <div className="gh-field__control">
        <input
          id={id}
          className="gh-field__input"
          type={isPassword && shown ? "text" : type}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          style={isPassword ? { paddingRight: 72 } : undefined}
          {...input}
        />
        {isPassword && (
          <button
            type="button"
            className="gh-field__toggle"
            onClick={() => setShown((s) => !s)}
            aria-pressed={shown}
          >
            {shown ? "Hide" : "Show"}
          </button>
        )}
      </div>
      {error ? (
        <p id={`${id}-err`} className="gh-field__error" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="gh-field__hint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------ Alert, Badge */
/** tone: info | success | warn | danger */
export function Alert({ tone = "info", children, className }) {
  return (
    <div
      className={cx("gh-alert", tone !== "info" && `gh-alert--${tone}`, className)}
      role={tone === "danger" || tone === "warn" ? "alert" : "status"}
    >
      <div>{children}</div>
    </div>
  );
}

/** tone: default | accent | ok | warn | danger */
export function Badge({ tone = "default", children }) {
  return <span className={cx("gh-badge", tone !== "default" && `gh-badge--${tone}`)}>{children}</span>;
}

/* ------------------------------------------------------------------- Card */
export function Card({ as: Tag = "div", flat, accent, className, children, ...rest }) {
  return (
    <Tag className={cx("gh-card", flat && "gh-card--flat", accent && "gh-card--accent", className)} {...rest}>
      {children}
    </Tag>
  );
}

export function Container({ className, children, as: Tag = "div", ...rest }) {
  return (
    <Tag className={cx("gh-container", className)} {...rest}>
      {children}
    </Tag>
  );
}

/* --------------------------------------------------- DimensionLine / Section */
/** ├──── 01 / LABEL ────┤ */
export function DimensionLine({ no, children, short }) {
  return (
    <div className="gh-dim" aria-hidden={!children ? true : undefined}>
      <span className="gh-dim__line gh-dim__line--short" />
      <span>
        {no && <span className="gh-dim__no">{no}</span>}
        {no && " / "}
        {children}
      </span>
      <span className={cx("gh-dim__line", short && "gh-dim__line--short")} />
    </div>
  );
}

export function SectionHeading({ no, eyebrow, title, lede, center, as: Tag = "h2", id }) {
  return (
    <header className={cx("gh-sh", center && "gh-sh--center")}>
      <DimensionLine no={no}>{eyebrow}</DimensionLine>
      <Tag className="gh-sh__title" id={id}>
        {title}
      </Tag>
      {lede && <p className="gh-sh__lede">{lede}</p>}
    </header>
  );
}

export function Section({ id, tint, labelledBy, className, children }) {
  return (
    <section id={id} aria-labelledby={labelledBy} className={cx("gh-section", tint && "gh-section--tint", className)}>
      <Container>{children}</Container>
    </section>
  );
}

/* ------------------------------------------------------------ ContourBar */
export function ContourBar({ min = "0", max = "σ max", label }) {
  return (
    <div className="gh-contour" role="img" aria-label={label || `Colour scale from ${min} to ${max}`}>
      <div className="gh-contour__bar" />
      <div className="gh-contour__labels">
        <span>{min}</span>
        <span>{max}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Modal */
/** Native <dialog>: focus trap, Esc to close and inert background for free. */
export function Modal({ open, onClose, title, children, actions }) {
  const ref = useRef(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="gh-modal" aria-labelledby={titleId} onClose={onClose} onCancel={onClose}>
      <div className="gh-modal__body">
        <h2 className="gh-modal__title" id={titleId}>
          {title}
        </h2>
        <div>{children}</div>
        {actions && <div className="gh-modal__actions">{actions}</div>}
      </div>
    </dialog>
  );
}

/* -------------------------------------------------------------- Accordion */
/** items: [{ q, a }]. Native <details>; `name` makes them mutually exclusive. */
export function Accordion({ items, name = "faq" }) {
  return (
    <div className="gh-acc">
      {items.map((it, i) => (
        <details key={it.q} className="gh-acc__item" name={name} open={i === 0 ? true : undefined}>
          <summary className="gh-acc__summary">{it.q}</summary>
          <div className="gh-acc__panel">{it.a}</div>
        </details>
      ))}
    </div>
  );
}
