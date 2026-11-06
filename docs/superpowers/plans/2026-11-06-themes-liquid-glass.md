# Themes (Liquid Glass + Normal) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Four looks (glass dark, glass light, normal dark, normal light), switched from the menu bar, remembered per user. The glass looks are redone as "crystal" glass on new backgrounds; the normal looks are solid.

**Architecture:**
- `<html data-look="glass|normal" data-mode="dark|light">` is rendered server-side from the user's saved choice, falling back to the `nv_theme` cookie, then to `glass` + `dark`.
- A new `static/theme.css` holds everything that differs between the looks:
  - the four token sets;
  - the four page backgrounds;
  - the glass-only material rules.
- `app.css` and the template `<style>` blocks use only tokens.
- The switch is JS (`setTheme`). It changes the attributes in place, saves through a small API, and fires a `themechange` event that charts listen to.

**Tech Stack:** Flask + Jinja, flask-sqlalchemy (SQLite), plain CSS custom properties, plain JS, Chart.js 4 (dashboard).

**Spec:** `docs/superpowers/specs/2026-11-06-themes-liquid-glass-design.md`. The look reference is `.superpowers/brainstorm/1627-1793984340/content/normal-theme.html`. Its CSS holds the exact token values for crystal glass (`.dark.crystal`, `.light.crystal`), normal (`.normal.dark`, `.normal.light`) and the backgrounds (`.bg-aurora2`, `.bg-day2`, `.bg-flat-dark`, `.bg-flat-light`).

## Global Constraints

- **Looks:** exactly two looks, `glass` and `normal`, and two modes, `dark` and `light`. Anything else is treated as the default `glass` + `dark`.
- **Glass panels ("crystal"):**
  - fill: white 3.5% → 0% in dark, white 18% → 3% in light;
  - blur: 3 px;
  - edge: a 1 px gradient edge, bright at top-left and bottom-right, plus an inner top highlight and a soft drop shadow. No grey border.
- **Tables, forms, code/config views:** the stronger "table" tint, dark `rgba(8,14,30,.16→.22)`, light `rgba(255,255,255,.34→.26)`.
- **Overlays** (dropdown menus, liquid select popover, toasts, tooltips, modals, the Port Locator dock):
  - glass: about 75–85% fill and a 20 px blur, with the same edge;
  - normal: solid.
- **Normal:** no `backdrop-filter` anywhere.
  - Dark: panels `#141a26` on `#0b0f17`, border `#232c3b`.
  - Light: panels `#ffffff` on `#f3f5f8`, border `#e2e7ee`.
- **Backgrounds:** static, fixed layers painted once. Nothing animates a `backdrop-filter`.
- **Boxes (option C):**
  - status indicators are a dot plus text;
  - other badges keep a soft fill with no outline;
  - secondary buttons have no outline;
  - primary and danger buttons stay filled;
  - table rows get faint lines only.
- **Primary button:** white with dark text in dark mode, near-black with white text in light mode.
- **Rack drawing colours** in `racks.html` (device faces and device blocks) are fixed in all four looks.
- **Contrast:** body and table text is at least 4.5:1 against the brightest glow it can sit over.
- **Commits and changelog:** no Claude attribution in commits. Add a CHANGELOG entry under `## [Unreleased]`; no version bump.

## Review Focus

1. **A bad stored value** (cookie `nv_theme=foo`, or a DB value outside the allowed set) renders `glass` + `dark`, with no 500 error → Task 1 test `bad_values_fall_back`.
2. **A user who logs in on a new browser** with no cookie gets their saved look on the first page, and the cookie is then set → Task 1 test `saved_theme_follows_user`.
3. **A read-only user** can save their own theme. It's personal, not an admin action → Task 1 test `read_only_can_save_theme`.
4. **The OS set to light** with the user's mode set to dark: the page stays dark. `prefers-color-scheme` never decides → Task 2 check `os_light_does_not_override`.
5. **Switching while the dashboard is open** updates the chart text and grid colours without a reload → Task 5 check `chart_follows_theme`.

**Where the checks live:** scratchpad scripts in `S = C:\Users\enawba\AppData\Local\Temp\2\claude\03c6a662c8477dd4\81b88e15-0f10-4b64-a16d-bbeca866c717\scratchpad`, as in earlier work:
- Python checks use `vt_fresh.fresh(name)` (a fresh copy of the database) and the Flask test client. Test logins are `t_ga`/`Passw0rd!x`, `t_sa`/`NewPass1!xx` and `t_rw`. For a read-only user, set `t_rw`'s role to `read-only` in the copy.
- Browser checks drive headless Edge over CDP on port 9333 against `nav_server.py` (port 5077, `MIMIC_IIS=1`). Copy the login/CDP header from `locator_render.mjs`.
- Write files containing backslashes with the Write tool. Bash heredocs halve backslashes here.

---

### Task 1: Theme choice: stored, rendered, saved

**Files:**
- Modify `network_vault/models.py`: add `User.theme_look = db.Column(db.String(8))` and `User.theme_mode = db.Column(db.String(8))`. Both are nullable and added by `_add_missing_columns()`.
- **New module `network_vault/theme.py`:**
  - `LOOKS = ("glass", "normal")`, `MODES = ("dark", "light")`, `DEFAULT = ("glass", "dark")`, `COOKIE = "nv_theme"`;
  - `resolve(user, cookie_value) -> tuple[str, str]`: the user's values if both are valid, else the cookie value parsed as `"<look>-<mode>"` if valid, else `DEFAULT`;
  - `cookie_value(look, mode) -> str`.
- Modify `network_vault/__init__.py`, in `inject()`:
  - expose `theme_look` and `theme_mode` from `theme.resolve(user_obj, request.cookies.get(theme.COOKIE))`;
  - add an `after_request` hook that sets the cookie when a logged-in user's resolved theme differs from the cookie. Cookie: 1 year, `SameSite=Lax`, `path=/`, `HttpOnly=False` (JS sets it too).
- Modify `network_vault/views.py`: add `POST /api/account/theme`. JSON `{look, mode}`, CSRF via `X-CSRF-Token`, any logged-in user. Returns `{"ok": true, "look", "mode"}` and sets the cookie. Invalid values give a 400 with `{"error"}`; no session gives a 401.
- Modify `network_vault/templates/base.html`:
  - `<html lang="en" data-look="{{ theme_look }}" data-mode="{{ theme_mode }}">`;
  - `<meta name="color-scheme" content="{{ theme_mode }}">`.
- Test: `S/theme_state_test.py`

**Interfaces:**
- Produces:
  - `theme.resolve`, `theme.cookie_value`, `theme.COOKIE`;
  - template variables `theme_look` and `theme_mode`;
  - `POST /api/account/theme {look, mode}` → `{ok, look, mode}`;
  - the `<html>` attributes `data-look` and `data-mode`.

- [ ] **Step 1: Write the check `theme_state_test.py`.** Each case reads the `data-look` and `data-mode` attributes from the rendered `<html>` tag.
  - `default_is_glass_dark`: a fresh login with no cookie and no saved value renders `data-look="glass" data-mode="dark"`.
  - `save_and_render`: POST `{"look": "normal", "mode": "light"}` → 200 and `{ok: true}`. The next GET `/` renders `normal`/`light`, and the response sets the cookie `nv_theme=normal-light`.
  - `saved_theme_follows_user`: a new test client (no cookie) logs in as the same user and `/` renders `normal`/`light`.
  - `bad_values_fall_back`:
    - POST `{"look": "x", "mode": "light"}` → 400, and the stored value is unchanged;
    - a client with cookie `nv_theme=foo` on `/login` renders `glass`/`dark`;
    - the DB value `theme_look="bogus"` renders `glass`/`dark`.
  - `login_page_uses_cookie`: a client with cookie `nv_theme=normal-light`, not logged in, gets `/login` rendering `normal`/`light`.
  - `read_only_can_save_theme`: `t_rw`, with its role set to `read-only`, POST → 200.
  - `csrf_required`: POST without the header → 400 or 403, as other APIs do.
- [ ] **Step 2: Run it.** `cd C:\MGM\mgm-network-vault; python S\theme_state_test.py`. Expected: FAIL (no route; attributes missing).
- [ ] **Step 3: Implement** the model columns, `theme.py`, the context and cookie hook, the route and `base.html` as listed in Files.
- [ ] **Step 4: Run it.** Expected: `ALL OK`.
- [ ] **Step 5: Commit.** `feat(theme): per-user look and mode, rendered on <html>, saved via API`

### Task 2: Token sets, backgrounds and glass material (`theme.css`)

**Files:**
- **New file `network_vault/static/theme.css`**, loaded in `base.html` before `app.css`, with the same `?v={{ app_version }}`.
  - Four blocks, `:root[data-look=glass][data-mode=dark]` and so on, each defining every token below. Values come from the mockup classes named in the Spec line above.
  - Page background layers on `body::after` (static, `position: fixed`, painted once): aurora + glows (glass dark), daylight mist + grid + glows (glass light), flat `#0b0f17` (normal dark), flat `#f3f5f8` (normal light). These replace the current `body::after` / `body::before` rules and `bg-liquid.jpg` in `app.css`. Keep the file; it is no longer referenced.
  - **Glass material** under `[data-look=glass]`:
    - **Panels** (`.card`, stat tiles, `.top-nav-bar`, `.top-subnav`, `.op-modal`, `.nav-dropdown-menu`, the liquid select popover, `.toast`) get `backdrop-filter: blur(var(--blur-panel|--blur-overlay)) saturate(170%)`.
    - **The gradient edge** is a `::before` with a 1 px padding mask, as in the mockup's `.glass::before`. Where one of those classes already uses `::before`, move that use to `::after` or a child element. Don't drop it.
    - **The lens** is the `.nav-item.active` / `.top-subnav a.active` treatment from the mockup's `.nav a.on`.
  - **Normal:** `[data-look=normal] * { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }` and no `::before` edge. This is the only `!important` allowed in `theme.css`.
  - `color-scheme: dark|light` follows `data-mode`. Delete every `@media (prefers-color-scheme …)` block in `app.css` and `base.html`.
- Test: `S/theme_tokens.mjs`

**Interfaces:**
- Produces the token names that all later tasks use and never redefine. These are the existing names, with values per look:
  - `--bg`, `--panel`, `--panel-strong`, `--panel-2`, `--border`, `--border-subtle`;
  - `--text`, `--text-dim`, `--muted`;
  - `--accent`, `--accent-2`, `--accent-soft`, `--accent-text`;
  - `--ok`, `--ok-soft`, `--warn`, `--warn-soft`, `--bad`, `--bad-soft`, `--info-soft`;
  - `--add`, `--add-strong`, `--del`, `--del-strong`;
  - `--shadow`, `--radius`, `--mono`.
- Plus these new tokens:
  - `--overlay` (overlay fill);
  - `--blur-panel` (3px glass / 0 normal) and `--blur-overlay` (20px / 0);
  - `--edge-hi`, `--edge-lo` (gradient edge colours), `--rim` (inner top highlight), `--glow` (inner bottom glow), `--drop` (shadow colour);
  - `--lens`, `--lens-rim`;
  - `--chip` (soft badge/button fill), `--chip-hover`, `--hair` (row lines);
  - `--input` (input fill);
  - `--btn-primary-bg`, `--btn-primary-fg`;
  - `--scroll-thumb`, `--scroll-thumb-hover`;
  - `--chart-text`, `--chart-grid`;
  - `--code-bg`.
- **Retiring old tokens:** `--side*`, `--gold*` and `--mgm-*` are kept as aliases of `--accent*` and the surface tokens until Task 6 removes their last uses.

- [ ] **Step 1: Write the browser check `theme_tokens.mjs`.** It loads `/` as `t_ga` and, for each of the four combos, sets the two `<html>` attributes directly.
  - `tokens_per_look`: `getComputedStyle(documentElement)` gives `--text`, `--bg`, `--panel-strong` and `--blur-panel`. The four sets are pairwise different. Normal dark has `--bg` `#0b0f17` and normal light has `#f3f5f8`.
  - `normal_has_no_blur`: in both normal combos, every element's computed `backdrop-filter` is `none`.
  - `glass_panels_blur`: in glass dark, `.card`'s `backdrop-filter` contains `blur(3px)` and `.nav-dropdown-menu`'s contains `blur(20px)`.
  - `os_light_does_not_override`: with `Emulation.setEmulatedMedia` set to `prefers-color-scheme: light` and `data-mode="dark"`, `--bg` stays the glass-dark value and `body`'s computed `color-scheme` is `dark`.
  - `no_js_errors`.
- [ ] **Step 2: Run it.** `node S\theme_tokens.mjs` (with the server and Edge running). Expected: FAIL.
- [ ] **Step 3: Implement** `theme.css` and the `base.html` link. Remove the old background rules and the `prefers-color-scheme` blocks.
- [ ] **Step 4: Run it.** Expected: `ALL OK`.
- [ ] **Step 5: Commit.** `feat(theme): theme.css with four token sets, new backgrounds, crystal glass material`

### Task 3: Shared components on tokens, fewer boxes

**Files:**
- Modify `network_vault/static/app.css`, in these sections: layout, cards, tables, badges, forms & buttons, the toggle switch, flash, code / diff, toast + modal, the liquid select (incl. popover and scrollbar), the calendar, the section label, role badges, multi-site tagging, global SVG icons.
  - Every colour is a token. No hex, `rgb(a)` or `hsl(a)` literals remain in these sections.
  - Remove `!important` wherever it only existed to beat another rule in `app.css`.
  - **Option C:**
    - status classes become a dot plus text: `.badge.ok`, `.badge.bad`, `.badge.warn`, `.status-*`, and whatever classes `devices.html`, `dashboard.html` and `device_detail.html` use for up/down/OK/failed (find them with grep);
    - other `.badge` / chip classes lose their border and use `--chip`;
    - `.btn` (secondary) loses its border and uses `--chip`, with `--chip-hover` on hover;
    - `.btn.primary` uses `--btn-primary-bg` / `--btn-primary-fg`;
    - `.btn.danger` stays filled with `--bad`;
    - table rows get `border-top: 1px solid var(--hair)` and no cell boxes;
    - **numbers on the dashboard stat tiles:** weight 300, size about 30 px.
  - Change markup only where a status isn't using a shared class.
- **New checker `S/theme_lint.py`.** It prints each colour literal it finds as `file:line literal`, and exits 1 if any are found.
  - **What counts as a colour literal:** `#[0-9a-fA-F]{3,8}\b`, `rgba?\(` or `hsla?\(`.
  - **Where it looks:** `app.css` outside `/* theme-exempt:start */ … /* theme-exempt:end */` blocks, and the `<style>` blocks and `style=""` attributes of the templates it is given on the command line.
  - **What it skips:** `url(#…)` SVG references and HTML entities like `&#123;`.
  - **Usage:** `python S\theme_lint.py --sections "<section header text>,…"` limits the check to the named `app.css` sections; `--all` checks everything. Section boundaries are the existing `/* … */` header comments.

**Interfaces:**
- Consumes: the tokens from Task 2.
- Produces: `theme_lint.py`, which later tasks run with more sections and templates.

- [ ] **Step 1: Write `theme_lint.py`.** Run it on the sections listed above. Expected: FAIL, listing literals.
- [ ] **Step 2: Add the browser check `S/theme_components.mjs`.** For each of the four looks, on `/devices`:
  - `status_is_dot`: the first row's status element has a transparent background and no border, and has a `::before` dot whose colour is `--ok`;
  - `secondary_button_no_outline`: the `Reset` button's `border-top-width` is `0px` or its border colour is transparent;
  - `primary_button_colours`: the background of `.btn.primary` equals `--btn-primary-bg`.
  It also saves a screenshot `S/shots/components_<look>_<mode>.png`.
- [ ] **Step 3: Run both.** Expected: FAIL.
- [ ] **Step 4: Convert the listed sections.**
- [ ] **Step 5: Run both.** Expected: the lint reports 0 literals in these sections, and the browser check is `ALL OK`. Look at the four screenshots: no grey outlines on badges or secondary buttons, and the glass panels show the background through.
- [ ] **Step 6: Commit.** `feat(theme): shared components on tokens; status as dot + text, softer badges and buttons`

### Task 4: Floating menu bar, sub-row strip and the switches

**Files:**
- Modify `network_vault/templates/base.html`:
  - in `.top-nav-right`, before `.nav-user-pill`, add two segmented controls: `[ Glass | Normal ]` and `[ ☀ | ☾ ]`, with `data-set-look` / `data-set-mode` buttons, `aria-pressed` and titles "Liquid glass", "Normal", "Day", "Night";
  - add the same two controls as two rows in the mobile drawer, above `.mobile-version-tag`.
- Modify `app.css`, in these sections: top navigation bar, brand group, back button, top nav links, dropdown menus, right nav controls, site switcher pill, user profile pill, mobile drawer, responsive top bar, contextual sub-nav bar.
  - All colours become tokens.
  - **The bar** floats: inset 12 px from the top and sides, full width, radius 999 px, using the glass panel material.
  - **The sub-row** is a second, slimmer floating strip under the bar, with the same material.
  - **The current page** gets the lens: the active `.nav-item`, the active dropdown button on Operations/Admin pages, and the active sub-row link.
  - **Normal:** the lens is replaced by an `--accent-soft` pill with `--accent` text.
  - The page-transition rule that keeps the top bar still must keep working.
- Modify `network_vault/static/app.js`:
  - `setTheme({look, mode})`:
    - sets the `<html>` attributes;
    - updates `aria-pressed` on every switch;
    - sets `document.cookie = "nv_theme=<look>-<mode>; path=/; max-age=31536000; samesite=lax"`;
    - POSTs `/api/account/theme` with the CSRF meta token;
    - dispatches `window.dispatchEvent(new CustomEvent("themechange", {detail: {look, mode}}))`;
    - if the POST fails, the page keeps the new look and calls `toast("Couldn't save your theme", "bad")`.
  - Clicking a switch calls `setTheme`.
- Test: `S/theme_switch.mjs`

**Interfaces:**
- Consumes: Task 1's API and attributes, and Task 2's tokens.
- Produces: `window.setTheme({look, mode})` and the `themechange` event, `{detail: {look, mode}}`.

- [ ] **Step 1: Write `theme_switch.mjs`** (as `t_ga`, on a fresh DB copy).
  - `switch_changes_look`: clicking "Normal" gives `data-look=normal` immediately, with no navigation (the `Page.loadEventFired` count is unchanged), and `aria-pressed` is true on Normal.
  - `switch_persists`: click "Day", reload, and `data-mode=light`. Clear cookies, log in again, and `data-mode=light`.
  - `themechange_fires`: a listener receives `{look: "normal", mode: "light"}`.
  - `lens_on_current_page`: on `/devices` in glass, the active nav item's computed `box-shadow` contains an inset highlight. On `/operations/port-locator`, the Operations dropdown button and the Port Locator sub-row link are both active.
  - `mobile_switches`: at 390 px width, open the drawer; both controls are visible and clicking "Night" sets `data-mode=dark`.
  - `no_js_errors`.
  - It saves screenshots of the bar in all four looks at 1600 and 390 px.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Implement.** Then run `theme_lint.py` with the nav sections added; expected 0 literals there.
- [ ] **Step 4: Run it, plus the earlier checks `locator_page_browser.mjs` and `locator_render.mjs`.** Expected: `ALL OK` for all three.
- [ ] **Step 5: Commit.** `feat(theme): floating menu bar with glass lens, sub-row strip, Glass/Normal and Day/Night switches`

### Task 5: Feature sections and charts

**Files:**
- Modify `app.css`, in these sections:
  - sidebar collapse, back button (inline), dashboard chart grid, floor card, header bar & site switcher, flash entrance;
  - Port Locator & Optical Health, Optics KPI summary, Operations progress modal & terminal, minimised docked widget, global Port Locator dock, hardware health dashboard;
  - responsive 1366×768, page transitions.
  - All colours become tokens. The terminal log area in the progress modal uses `--code-bg`.
  - The dock and the progress modal use the overlay material.
- Modify `network_vault/templates/base.html`: replace its `<style>` scrollbar block with tokens `--scroll-thumb` and `--scroll-thumb-hover`, and drop its `prefers-color-scheme` block.
- Modify `network_vault/templates/dashboard.html`:
  - chart colours come from `--chart-text`, `--chart-grid`, `--accent`, `--warn`, `--ok` and `--bad`, read with `getComputedStyle`. The `isDark` `matchMedia` check is removed;
  - on `themechange`, re-read the tokens and `chart.update()` both charts. Keep chart references in variables rather than relying on `Chart.getChart`.
- Test: `S/theme_charts.mjs`

**Interfaces:**
- Consumes: the `themechange` event (Task 4) and the tokens (Task 2).

- [ ] **Step 1: Write `theme_charts.mjs`.**
  - `chart_follows_theme`: on `/` in glass dark, record `Chart.getChart('activityChart').options.scales.x.ticks.color`. Call `setTheme({look: "normal", mode: "light"})`, wait 300 ms; the colour now equals the normal-light `--chart-text`, with no reload.
  - `dock_uses_overlay`: on `/operations/port-locator`, open the progress modal by calling `openLocatorProgressModal()` (or the page's equivalent, found with grep). Its computed background matches `--overlay` in all four looks.
  - `no_js_errors`.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Convert** the sections, the base scrollbar styles and the dashboard charts.
- [ ] **Step 4: Run it, plus `theme_lint.py --all`.** Expected: `ALL OK`, and the lint shows 0 literals in `app.css` outside the exempt blocks. Template literals are fixed in Task 6.
- [ ] **Step 5: Commit.** `feat(theme): feature sections and dashboard charts follow the theme`

### Task 6: Page styles in templates

**Files:**
- Modify `network_vault/templates/`:
  - `account.html`, `racks.html`, `vlan_audit.html`, `ipam.html`, `lifecycle.html`: their `<style>` blocks;
  - those templates plus `alerts.html`, `health.html`, `optics.html`, `port_locator.html`, `dashboard.html`, `settings.html`, `reset_password.html`, `forgot_password.html` and `login.html`: their `style=""` attributes.
  - All colours become tokens.
  - In `racks.html`, the device-face and device-block colours go in a `/* theme-exempt:start */ … /* theme-exempt:end */` block, defined once as local custom properties (e.g. `--rack-face`) and used from there. The panel around the rack follows the theme.
  - In `vlan_audit.html`, status and code colours map to `--ok`, `--warn`, `--bad`, `--accent` and their `-soft` variants. A meaning those don't cover gets a new token in `theme.css`, defined in all four sets.
  - Remove the last uses of `--side*`, `--gold*` and `--mgm-*`, then delete those aliases from `theme.css`.
- Test: `theme_lint.py --all` over every template, plus `S/theme_pages.mjs`.

- [ ] **Step 1: Run `theme_lint.py --all` with all of `templates/*.html`.** Expected: FAIL, listing the template literals.
- [ ] **Step 2: Write `theme_pages.mjs`.**
  - It visits every page in the spec's Goals list, in all four looks. For pages needing an id, use the first device or backup. Log out for the login, forgot and reset pages.
  - It saves `S/shots/pages/<page>_<look>_<mode>.png`.
  - `no_js_errors` on every page.
  - `no_dark_island_in_light`: in both light looks, no visible element outside `.rack*` has a computed background with luminance below 0.2 and an area above 2,000 px².
- [ ] **Step 3: Convert the templates.**
- [ ] **Step 4: Run the lint and `theme_pages.mjs`.** Expected: 0 literals outside the exempt blocks, and `ALL OK`.
- [ ] **Step 5: Commit.** `feat(theme): page styles on tokens; rack hardware colours fixed`

### Task 7: Readability check, full regression, changelog

**Files:**
- **New `S/theme_contrast.py`:**
  - for glass dark and glass light, it composites the panel fill (`--panel`), the table tint (`--panel-strong`) and the overlay fill (`--overlay`) over the brightest glow colour of that look's background (the centre colours of its radial gradients) and over the base colour;
  - it computes the WCAG contrast of `--text`, `--text-dim` and `--muted` against each result, and of overlay text against the table tint;
  - it fails below 4.5:1 for `--text` / `--text-dim` and below 3:1 for `--muted`. Token values are read from `theme.css`.
  - **If a look fails:** raise that panel's tint (spec: "raise that panel's tint rather than changing the background"), then re-run the check.
- Modify `CHANGELOG.md`, under `## [Unreleased]` → `### Added`: Glass/Normal and Day/Night switches remembered per user; the redone liquid glass (aurora / daylight backgrounds, clear glass with light-catching edges, floating menu with a glass lens); status as dot + text; softer badges and buttons.
- Add `.superpowers/` to `.gitignore`.

- [ ] **Step 1: Write and run `theme_contrast.py`.** Fix any tints until it passes.
- [ ] **Step 2: Run every check:**
  - `theme_state_test.py`, `theme_tokens.mjs`, `theme_components.mjs`, `theme_switch.mjs`, `theme_charts.mjs`, `theme_pages.mjs`, `theme_lint.py --all`;
  - plus the earlier `locator_query_test.py`, `locator_trace_test.py`, `locator_page_browser.mjs` and `locator_render.mjs`.
  Expected: all `ALL OK`.
- [ ] **Step 3: Review the page screenshots** side by side for each look. Note any page that looks wrong and fix it in the owning section.
- [ ] **Step 4: Commit.** `docs: changelog for themes; ignore .superpowers`
