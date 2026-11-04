import React, { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Logo } from "../brand/Logo.jsx";
import TitleBlock from "../brand/TitleBlock.jsx";
import { Button, Container } from "../ui";
import { useAuth } from "../auth/AuthContext.jsx";
import { useTheme } from "../hooks/hooks.js";
import "./layout.css";

export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  const dark = theme === "dark";
  return (
    <button
      type="button"
      className="gh-iconbtn"
      onClick={toggle}
      aria-label={dark ? "Switch to light theme (vellum)" : "Switch to dark theme (blueprint)"}
      title={dark ? "Vellum (light)" : "Blueprint (dark)"}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        {dark ? (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </>
        ) : (
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        )}
      </svg>
    </button>
  );
}

const NAV = [
  { href: "/#features", label: "Features" },
  { href: "/#workflow", label: "Workflow" },
  { to: "/pricing", label: "Pricing" },
  { href: "/#faq", label: "FAQ" },
];

export function SiteHeader() {
  const { user, isPro, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const nav = useNavigate();

  useEffect(() => setOpen(false), [loc.pathname, loc.hash]);

  const signOut = () => logout().finally(() => nav("/"));

  return (
    <header className="gh-header">
      <Container className="gh-header__inner">
        <Logo />
        <nav className={`gh-nav${open ? " is-open" : ""}`} aria-label="Primary" id="primary-nav">
          {NAV.map((n) =>
            n.to ? (
              <NavLink key={n.label} to={n.to} className="gh-nav__link">
                {n.label}
              </NavLink>
            ) : (
              <Link key={n.label} to={n.href} className="gh-nav__link">
                {n.label}
              </Link>
            )
          )}
          <div className="gh-nav__actions">
            {user ? (
              <>
                <Button to="/account" variant="ghost" size="sm">
                  {isPro && <span className="gh-pro-dot" aria-hidden="true" />}
                  Account
                </Button>
                <Button to="/app" size="sm">
                  Open workspace
                </Button>
                <button type="button" className="gh-nav__signout" onClick={signOut}>
                  Sign out
                </button>
              </>
            ) : (
              <>
                <Button to="/login" variant="ghost" size="sm">
                  Sign in
                </Button>
                <Button to="/register" size="sm">
                  Start free
                </Button>
              </>
            )}
          </div>
        </nav>
        <div className="gh-header__tools">
          <ThemeToggle />
          <button
            type="button"
            className="gh-iconbtn gh-burger"
            aria-expanded={open}
            aria-controls="primary-nav"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((o) => !o)}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              {open ? <path d="M5 5l14 14M19 5L5 19" /> : <path d="M3 7h18M3 12h18M3 17h18" />}
            </svg>
          </button>
        </div>
      </Container>
    </header>
  );
}

export function SiteFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="gh-footer">
      <Container>
        <div className="gh-footer__top">
          <Logo />
          <ul className="gh-footer__links">
            <li><Link to="/pricing">Pricing</Link></li>
            <li><Link to="/terms">Terms</Link></li>
            <li><Link to="/privacy">Privacy</Link></li>
            <li><Link to="/login">Sign in</Link></li>
          </ul>
        </div>
        <TitleBlock
          items={[
            { k: "Title", v: "Gearhead FEA — browser-based finite element analysis", wide: true },
            { k: "Sheet", v: "1 of 1" },
            { k: "Scale", v: "1 : 1" },
            { k: "Rev", v: `${year}.${String(new Date().getMonth() + 1).padStart(2, "0")}` },
            { k: "Solver", v: "Standalone FE · Tet/Hex" },
            { k: "Currency", v: "PHP · GCash" },
            { k: "Status", v: "Released" },
            { k: "©", v: `${year} Gearhead` },
          ]}
        />
      </Container>
    </footer>
  );
}

/** Header + page + footer for public and account pages. */
export function SiteLayout() {
  return (
    <div className="gh-sheet">
      <a className="gh-skip" href="#main">Skip to content</a>
      <SiteHeader />
      <main id="main" className="gh-main">
        <Outlet />
      </main>
      <SiteFooter />
    </div>
  );
}

/** Split screen for sign in / sign up: form on the left, a drawing on the right. */
export function AuthLayout({ title, subtitle, children, aside }) {
  return (
    <div className="gh-sheet">
      <a className="gh-skip" href="#main">Skip to content</a>
      <div className="gh-auth">
        <section className="gh-auth__form" id="main">
          <Logo />
          <div className="gh-auth__card">
            <h1 className="gh-auth__title">{title}</h1>
            {subtitle && <p className="gh-auth__sub">{subtitle}</p>}
            {children}
          </div>
          <p className="gh-auth__legal">
            By continuing you agree to the <Link to="/terms">Terms</Link> and <Link to="/privacy">Privacy Policy</Link>.
          </p>
        </section>
        <aside className="gh-auth__aside" aria-hidden="true">
          {aside}
          <div className="gh-auth__theme"><ThemeToggle /></div>
        </aside>
      </div>
    </div>
  );
}
