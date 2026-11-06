# Snappy Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Menu-to-menu navigation feels instant: no artificial delay, a native cross-fade, hover prefetch, long-cached CSS/JS.

**Architecture:** Server side, one `after_request` hook sets cache headers on `/static/*`. Client side, the JS click interceptor and `.main` page animations are deleted and replaced by CSS `@view-transition` plus a Speculation Rules `<script>` in `base.html`.

**Tech Stack:** Flask, Jinja, plain CSS/JS. Chrome/Edge cross-document View Transitions and Speculation Rules (other browsers ignore both).

**Spec:** `docs/superpowers/specs/2026-11-06-snappy-navigation-design.md`

## Global Constraints

- Prefetch only, never prerender. `eagerness: "moderate"`.
- Prefetch limited to nav links: `.nav-brand`, `.nav-item`, `.dd-item`, `.subnav-link`, `.mobile-nav-drawer a`.
- Never prefetch `/logout`, `[download]`, `[data-no-transition]`, or paths matching `(\.csv|\.diff|\.zip|/zip|/download|/export[^/]*|/package|/config-from-export)$`.
- `/static/*` with a `v` query arg → `Cache-Control: public, max-age=31536000, immutable`; other `/static/*` → `public, max-age=86400`; non-static responses are untouched.
- Cross-fade around 150 ms; no animation under `prefers-reduced-motion: reduce`.
- Keep `app.css` one-property-per-line formatting; preserve CRLF line endings in edited files.
- No Claude attribution in commit messages.
- Do not bump `version.py` (the updater would advertise a release that has no tag); log the change under `## [Unreleased]` in `CHANGELOG.md`.

## Review Focus

1. Hovering "Logout" must not log the user out → Task 2 asserts no rule can match `/logout`.
2. Hovering "Export & migrate" must not start a download → Task 2 asserts the exclusion list is in the rules (the menu link itself is a page and may be prefetched).
3. Back/forward must not show a blank or faded page → Task 2 removes every `page-leaving` reference; checked by grep.
4. A release must still deliver new CSS/JS despite the 1-year cache → Task 1 asserts `base.html` links CSS and JS with `?v={{ app_version }}`.
5. Downloads and API responses must not become cacheable → Task 1 asserts `/logout` and an `/api/` response carry no `immutable` header.

There's no pytest suite in this repo. Checks are standalone scripts in the session scratchpad, run against the copy of the live DB in `scratchpad/vt` (same harness as `perf_test.py`).

---

### Task 1: Cache headers for static files

**Files:**
- Modify: `network_vault/__init__.py` (inside `create_app()`, next to `guard()`)
- Test: `scratchpad/nav_cache_test.py`

**Interfaces:**
- Produces: `@app.after_request def static_cache_headers(resp) -> Response`

- [ ] **Step 1: Write the check.** Logged in as `t_ga`:
  - `GET /static/app.css?v=x` → status 200 and `"immutable" in Cache-Control` and `"max-age=31536000"`.
  - `GET /static/mgm-logo.png` → `"max-age=86400"` and not `"immutable"`.
  - `GET /api/jobs` (or any `/api/` JSON route) and `GET /devices` → `"immutable"` not in `Cache-Control`.
  - `base.html` source contains `app.css') }}?v={{ app_version }}` and `app.js') }}?v={{ app_version }}`.
- [ ] **Step 2: Run it.** Expect FAIL on the first assertion.
- [ ] **Step 3: Implement** `static_cache_headers`. Act only when `request.path.startswith("/static/")` and `resp.status_code` is 200 or 304. Set `resp.cache_control.public = True`, `max_age` per the Global Constraints, and `immutable` when `"v" in request.args`.
- [ ] **Step 4: Run the check.** Expect ALL OK.
- [ ] **Step 5: Commit** `perf: long-cache versioned static files`.

### Task 2: Remove the fade delay, add the cross-fade and hover prefetch

**Files:**
- Modify: `network_vault/static/app.js:280-330`: delete `shouldAnimate`, the document `click` interceptor and the `pageshow` handler. Keep the `.nav-ripple` handler above them.
- Modify: `network_vault/static/app.css`:
  - Remove the `page-enter` and `page-fade-out` keyframes, the `.main { animation: page-enter … }` rule and the `.layout.page-leaving .main` rule.
  - Drop `.main` and `.layout.page-leaving .main` from the reduced-motion selector list (keep `.login-wrap .card`).
- Modify: `network_vault/static/app.css`: add a view-transition block at the end of the file:
  - `@view-transition { navigation: auto; }`
  - `.top-nav-bar { view-transition-name: topnav; }`
  - `::view-transition-group(topnav)`, `::view-transition-old(topnav)` and `::view-transition-new(topnav)` → `animation: none`
  - `::view-transition-old(root)` and `::view-transition-new(root)` → `animation-duration: 150ms`
  - Inside `@media (prefers-reduced-motion: reduce)`, `::view-transition-group(*)`, `::view-transition-old(*)` and `::view-transition-new(*)` → `animation: none`
- Modify: `network_vault/templates/base.html`: add a `<script type="speculationrules">` just before the `app.js` script tag:
  - JSON with a single `prefetch` rule, `eagerness: "moderate"`.
  - `where.and` = `selector_matches` on the nav selectors from the Global Constraints, then `not` `href_matches` for `/logout` and for each download pattern (written as URL patterns, e.g. `/*.csv`, `/*/zip`, `/*/download`, `/*/export*`, `/*/package`, `/*/config-from-export`), then `not` `selector_matches` `[download], [data-no-transition]`.
- Test: `scratchpad/nav_ui_test.py`

**Interfaces:** none consumed or produced. Independent of Task 1.

- [ ] **Step 1: Write the check.**
  - The `app.js` text contains neither `page-leaving` nor `setTimeout(() => { location.href`.
  - The `app.css` text contains no `page-fade-out` or `page-leaving`, and does contain `@view-transition`.
  - For each of the 17 menu pages (list in `nav_time.py`), as `t_ga`: status 200, and the HTML contains exactly one `type="speculationrules"`.
  - Parse that block as JSON:
    - The `prefetch` list exists and there's no `prerender` key.
    - The JSON text contains `"/logout"`, `".csv"`, `"zip"`, `"download"`, `"export"`, `"package"` and `"[download]"`.
- [ ] **Step 2: Run it.** Expect FAIL.
- [ ] **Step 3: Implement** the three file changes above.
- [ ] **Step 4: Run the check.** Expect ALL OK. Also run `node --check network_vault/static/app.js`.
- [ ] **Step 5: Browser check.**
  - If headless Edge or Chrome is available, load `/devices` from a local dev server, then click to `/backups`.
  - Confirm no console errors, and that hovering the Logout link triggers no request to `/logout` (server log).
  - Otherwise, list the checks for the user to do in their own browser.
- [ ] **Step 6: Commit** `perf: instant menu navigation with native cross-fade and hover prefetch`. Also commit the spec, the plan, and a CHANGELOG `## [Unreleased]` entry under **Changed**.
