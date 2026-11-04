# Design System — "The Drawing Sheet"

## Concept
Gearhead FEA looks like an **engineering drawing**, because that is what the product produces: a part, its
constraints, and a stress contour. Every screen is a sheet.

| Element | Expression |
|---|---|
| Paper | Light theme **"vellum"** (warm paper, ink-black linework). Dark theme **"blueprint"** (cyanotype navy, chalk linework). Both sit on a faint 24 px drafting grid with 120 px majors. |
| Signature colour | **Stress red** `#c8361b` — the hot end of a contour plot. Used for actions and emphasis only. |
| Contour ramp | Six stops `--gh-c0…c5` (blue → cyan → green → yellow → orange → red). Appears in the hero animation, headline underline, step rulers, legend bars, logo. |
| Typography | **Space Grotesk** (display, semibold, tight tracking; emphasis is colour, not italics) · **Hanken Grotesk** (body) · **JetBrains Mono** (labels, numbers). Self-hosted via `@fontsource` — no third-party font requests. |
| Dimension lines | Section eyebrows are drawn as `├── 01 / CAPABILITIES ──┤`, like a dimension callout. |
| Registration marks | Cards carry red corner marks on two opposite corners. |
| Hard shadows | Buttons/figures use offset, un-blurred shadows (a "stamped" look), not soft glows. |
| Title block | The footer is a real drawing title block (title, sheet, scale, rev, status…). |
| Logo | A hexagonal nut cut into six mesh triangles, each tinted with a ramp stop. |
| Hero | Live canvas FEA: a meshed cantilever bends under a pulsing tip load, shaded by bending stress. Honors `prefers-reduced-motion`, pauses off-screen, follows the theme. |

## Tokens
`frontend/src/styles/tokens.css` — all prefixed `--gh-` (paper, ink, rule, accent, status colours, ramp, fonts,
gutter, header height). Light is default; `[data-theme="dark"]` overrides. The theme toggle writes
`localStorage["webfea-theme"]`, shared with the simulator workspace.

## Reusable components
| Location | Components |
|---|---|
| `src/ui/index.jsx` | `Button` (primary/secondary/ghost/danger, sm/md/lg, `to`/`href`, loading), `TextField` (label, hint, error, password reveal), `Alert`, `Badge`, `Card` (flat/accent), `Container`, `Section`, `SectionHeading`, `DimensionLine`, `ContourBar`, `Modal` (native `<dialog>`), `Accordion` (native `<details>`), `Spinner` |
| `src/brand/` | `Logo`, `LogoMark`, `TitleBlock` |
| `src/layout/Layout.jsx` | `SiteLayout`, `SiteHeader` (responsive menu), `SiteFooter`, `AuthLayout`, `ThemeToggle` |
| `src/site/` | `MeshHero`, `PricingTable`, homepage sections (`Hero`, `Features`, `Workflow`, `PricingSection`, `FaqSection`, `CtaBand`) |
| `src/auth/AuthContext.jsx` | `AuthProvider`, `useAuth`, `RequireAuth`, `GuestOnly`, `UpgradeListener` |
| `src/hooks/hooks.js` | `usePageTitle`, `useTheme`, `useCheckout`, `formatDate`, `peso` |

## Accessibility
Skip link; visible focus ring; labelled inputs with `aria-invalid`/`aria-describedby`; `role="alert"` on errors;
native dialog/disclosure semantics; reduced-motion support; text/accent contrast ≥ 4.5:1 for body and buttons;
layout verified at 390 px and 1440 px.

## Isolation from the workspace
The simulator's legacy stylesheet uses element selectors (`button`, `label`, `select`). It is imported as a string
(`styles.css?inline`) and injected into `<head>` **only while `/app` is mounted**, so it cannot affect the site.
The workspace (and three.js, ~230 kB gz) is a lazy chunk; the landing bundle is ~90 kB gz JS + 11 kB gz CSS.
