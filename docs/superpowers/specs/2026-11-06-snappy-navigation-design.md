# Snappy page-to-page navigation — design

**Date:** 2026-11-06 · **Size:** small change

## Why
Moving between menu pages feels slow. Measured: every page renders on the server in 10–30 ms, so the delay is in the browser — an artificial 140 ms fade-out before navigating, a 280 ms fade-in after, CSS/JS revalidated on every click, and a blank flash between pages.

## Goal / non-goals
- **Goal:** clicking a menu item feels instant, with a smooth cross-fade and a header that stays put.
- **Non-goal:** turning the app into a single-page app; changing any page's own behaviour.
- **Anti-goal:** a link that should download a file or log out gets prefetched or broken; the page loading slower in any browser.

## Approaches
1. Cut the waiting only — fast, but keeps the blank flash.
2. **Cut the waiting + native cross-document view transitions (chosen).** Small, CSS-led, degrades to instant navigation where unsupported (Firefox).
3. Swap only the content area (Turbo-style) — rejected: 13 pages have their own start-up scripts and live streams that would need rework, for little gain at 10–30 ms server times.

## Design
- **Remove the artificial delay:** delete the click interceptor that fades `.main` out and waits 140 ms, its `pageshow` undo, and the `.main` `page-enter` / `page-fade-out` animations. The menu ripple and the login card animation stay.
- **Cross-fade:** `@view-transition { navigation: auto; }`. The top nav bar gets its own `view-transition-name` with no animation, so it doesn't flicker; the content area cross-fades in ~150 ms. Turned off under `prefers-reduced-motion`.
- **Hover prefetch:** a Speculation Rules block in `base.html` (`eagerness: moderate` — fetches after ~200 ms hover or on press), limited to links in the top nav / mobile nav, and excluding `/logout` and anything matching the existing download pattern (`.csv`, `.zip`, `/download`, `/export*`, `/package`, …), plus `[download]` and `[data-no-transition]` links. Prefetch only — never prerender, so no page scripts or streams run early.
- **Static caching:** `/static/*` requested with `?v=` (CSS, JS — already version-tagged) gets `Cache-Control: public, max-age=31536000, immutable`; other static files (images) get `max-age=86400`. Nothing else changes, so downloads and API responses are unaffected.

## Builder's calls
- Exact cross-fade duration and easing.
- How the CSS is organised; keep the existing one-property-per-line formatting in `app.css`.

## Verification
- Every menu page still returns 200 and no JS errors on load.
- `/static/app.css?v=…` returns the long-cache header; a backup download and `/logout` don't.
- Logout, CSV/ZIP exports and backup downloads still work from the menu and from their pages.
