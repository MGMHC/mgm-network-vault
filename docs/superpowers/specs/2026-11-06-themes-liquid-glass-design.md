# Themes: clearer liquid glass, plus a Normal theme

Date: 2026-11-06 · Status: draft for approval

## Intent

Enawba finds the current liquid glass look "not that great": the panels read as dark navy boxes with grey outlines, not glass. They want:

1. a liquid glass theme that actually looks and feels like glass (references: Apple "Liquid Glass" Dribbble shots - a smart-home dashboard over a blurred room, a glass "lens" on a pill tab bar, a floating pill menu over a photo, a settings card over a landscape);
2. a switch to a plain "Normal" theme.

The decisions below were made together on mockups (`.superpowers/brainstorm/1627-1793984340/content/`: `backgrounds.html`, `glass-clarity.html`, `normal-theme.html` - the last one is the reference for the look).

## What the user decided

- **Liquid glass, dark:** background **B "Aurora"** - large soft blobs of MGM blue, teal, violet and amber on deep navy, plus a few smaller, brighter glows so the glass has something to show through. No photo.
- **Liquid glass, light:** background **D "Daylight mist"** - pale grey-blue, faint 64 px grid, soft pastel blobs and the same few glows.
- **Glass clarity: "Crystal"** - almost no tint (white 3.5% → 0% dark; 18% → 3% light), 3 px blur, and light-catching edges (a 1 px gradient edge bright at top-left and bottom-right, an inner top highlight, a soft drop shadow) instead of grey borders. Tables keep a slightly stronger tint (mockup: dark `rgba(8,14,30,.16-.22)`, light `rgba(255,255,255,.26-.34)`).
- **Normal theme:** same layout, spacing and menu as glass, but solid panels with a thin border, plain page background, no blur, no see-through. Dark: slate panels `#141a26` on `#0b0f17`. Light: white panels on `#f3f5f8`.
- **Two independent switches:** Glass / Normal and Day / Night - four looks in all.
- **Shared look for both themes** (from the references): one floating pill menu with a glass "lens" on the current page; big light-weight numbers; quieter labels; solid white (dark mode) / near-black (light mode) primary button.
- **Fewer boxes - "in between" (option C):** status indicators (up/down, OK/failed, reachable, online) become a coloured dot plus text, no pill. Other badges and chips (site codes, roles, severities, VLAN codes, counts) keep their shape with a soft fill and no outline. Secondary buttons lose their outline (soft fill, or text-only for low-priority actions like Edit/Reset); primary and danger buttons stay filled. Table cells and rows have no boxes, only faint row lines. This is mostly CSS (the shared badge / button classes); markup changes only where a status is not using a shared class. Rejected: re-skin only (still boxy), full conversion of every pill (~25 pages of markup; some pills are clearer as pills).

## Goals

- All four looks work on every page: dashboard, devices, device detail, backups, backup view, compare, schedules, logs, sites, credentials, racks, IPAM, VLAN audit, lifecycle, health, optics, port locator, alerts, settings, account, GitHub, migrate, login / forgot / reset password, restarting.
- Also every shared component: top menu, dropdown menus, the custom "liquid" select popover (`app.js`), modals (incl. Port Locator progress modal and the minimised locator dock), toasts, flash messages, tooltips, scrollbars, code/config views and diffs (add/del colours), charts on the dashboard (Chart.js reads CSS variables).
- The switch is in the top menu bar, one click each, takes effect immediately without a page reload.
- The choice is remembered **per user account**, so it follows them to any PC.

## Non-goals

- No changes to page content, features or information layout beyond the menu bar.
- No new mobile design; the existing mobile menu must keep working in all four looks.
- No user-uploaded or photo wallpapers.

## Anti-goals (a failure even if it "works")

- Small text (IPs, times, table rows, form labels) that is hard to read over a bright glow. Body text must meet WCAG AA contrast (4.5:1) against the brightest part of the background it can sit on.
- A flash of the wrong theme when a page loads.
- Slower navigation or scrolling. Earlier performance work (v1.15: static fixed background layers, no animated full-screen blur) must not regress. Normal is also the light option for older PCs: no backdrop-filter at all.
- Pages that are half-converted: some hard-coded colour still showing the old theme in one of the four looks.

## Constraints

- Flask + Jinja, one `static/app.css` (≈4,700 lines, ≈400 hard-coded colours outside `:root`, ≈250 `!important`), no CSS build step, served via IIS; static files are cached with `?v={{ app_version }}`.
- Page-level styles also live in templates: `account.html` (540 lines of `<style>`), `racks.html` (250, 70 colours), `vlan_audit.html` (234, 56 colours), `ipam.html`, `lifecycle.html`, `base.html`; many inline `style=""` attributes use `var(--…)` already, some use raw colours.
- `base.html` and `app.css` currently force dark (`<meta name="color-scheme" content="dark">`, `color-scheme: dark`); a few `prefers-color-scheme` blocks exist and must stop deciding the theme.
- Browsers: Edge / Chrome on Windows.
- Existing users have no stored preference.

## Approaches considered

1. **One set of tokens, switched by two attributes on `<html>` - chosen.** Every colour, edge, tint and blur in the CSS and the template styles comes from a named token (CSS custom property). `data-look="glass|normal"` and `data-mode="dark|light"` on `<html>` select one of four token sets; a few glass-only rules (backdrop-filter, the gradient edge, the lens) apply under `[data-look=glass]`. Most work up front (moving the hard-coded colours onto tokens), but each page switches cleanly and later changes are made once.
2. Four separate stylesheets. Simple to reason about; every later change made four times; they drift.
3. An override sheet layered on top of today's CSS. Fastest to start; must out-shout ~250 `!important` rules and every hard-coded colour leaks through - patchy.

## Builder's calls already made (Claude's, open to change)

- **Where the choice is stored:** two columns on `User` (`theme_look`, `theme_mode`), added by the existing `_add_missing_columns()` migration, plus a cookie mirror (`nv_theme`, e.g. `glass-dark`) so the login / forgot / reset pages (no user yet) and the first paint use it. `base.html` renders `data-look` / `data-mode` on `<html>` server-side, so there is no flash. Changing the switch updates the attributes in place and saves through a small POST endpoint (CSRF-protected like the others).
- **Default:** users with no stored choice get **glass + dark** (closest to today).
- **Menu bar shape:** a floating rounded bar inset from the page edges and as wide as the page, not a small centred pill - it holds about ten items plus the site picker, user and version. The current page gets the glass lens (glass) or a soft tinted pill (normal).
- **Switch control:** two small segmented pills in the menu bar, right side before the user menu: `[ Glass | Normal ]` and `[ ☀ | ☾ ]`. On the mobile menu they appear as two rows.
- **Readability guard for crystal:** tables, forms, code/config views and modals use the slightly stronger "table" tint from the mockup; text on glass gets a faint text shadow in dark mode. If a page fails the contrast check, raise that panel's tint rather than changing the background.
- **Performance guard:** backgrounds stay static, fixed layers painted once (as today). Blur only on panels, 3 px. Nothing animates a backdrop-filter.
- **Charts:** Chart.js colours read from tokens and are refreshed when the theme switches.
- **Second menu row stays.** The Operations / Admin sub-row (`.top-subnav` in `base.html`) keeps its links and behaviour; it becomes a second, slimmer floating strip under the main bar, with the lens on the current sub-page. The main bar's lens sits on Operations / Admin when one of their pages is open.
- **Things that float over content are more solid.** Dropdown menus, the custom select popover, toasts, tooltips, modals and the minimised Port Locator dock sit over tables and text, not over the soft background, so crystal tint would let rows show through and clash. In glass they use a strong tint (about 75-85% fill) with a 20 px blur and the same light-catching edge; in normal they are solid. They are few and short-lived, so the heavier blur does not affect scrolling. The contrast check covers them too (text on the popover against a table row behind it).
- **Colours that carry meaning.** The rack drawing in `racks.html` keeps fixed "hardware" colours (dark device faces, coloured device blocks) in all four looks; only the panel around it follows the theme. It is explicitly exempt from the "no leftover old-theme colour" rule. Status and code colours (VLAN audit cells, health / optics states, alert severities, diff add/del, up/down) become tokens with a dark-mode and a light-mode shade, so they stay readable on white.

## Left to the builder

- Token names and how the 4,700-line stylesheet is reorganised.
- Exact hover / focus / pressed states, as long as they follow the four looks.
- The order pages are converted in.

## How we know it's done

- Screenshots of every page above in all four looks, reviewed side by side; none shows a leftover old-theme colour.
- A contrast check of body and table text against the brightest background glow, glass dark and glass light.
- Switching updates the page immediately, survives reload and logout/login, and follows the user to another browser.
- Existing browser checks (navigation, Port Locator, alerts) still pass; no JS errors.
