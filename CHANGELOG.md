## [Unreleased]

## [1.20.0] - 2026-11-07

### Added
- **Backup schedules: pick several targets and exclude devices**: Groups, Roles and Platforms are now multi-select (for example Level-4 and Level-5, or core and distribution), and a new **Exclude devices** list skips chosen switches even when they match, e.g. "all of Level-4 except two". The device lists are searchable. The Schedules table and the Dashboard show every picked value and what is excluded. Existing schedules keep working unchanged. A schedule that targets groups, roles or platforms must have at least one picked.

### Fixed
- Empty dates (e.g. "Last run" of a schedule that never ran) showed garbled characters instead of a dash.

## [1.19.1] - 2026-11-07

### Added
- **Working animation in scan windows**: Hardware Health, Optical Health and the Port Audit ("Probe Live Link States") now show a slowly turning dotted globe in the log panel while a scan runs, with a caption naming the stages every scan goes through and the elapsed time. When live log lines arrive it shrinks to a small icon in the panel header; it stops when the scan finishes. It keeps turning, at half speed, when Windows animations are off (the browser then asks pages to reduce motion), since it is the only sign the scan is alive.

### Fixed
- **Port Audit window layout**: the log panel of the "Probing Live Switch Port Operational Status" window had no height and its header wrapped, because it used style classes that did not exist. It now matches the other scan windows.

## [1.19.0] - 2026-11-07

### Added
- **MAC/IP history** (Operations > MAC/IP History): every few hours the vault reads the MAC table of each switch (and the ARP table of the distribution/core switches) and remembers where each MAC address was seen: switch, port, VLAN, IP, first seen and last seen. Search by MAC in any format, IP, switch name or port. Uplink ports are left out, so only the port a device is really plugged into is kept. It is off until a Group Administrator turns it on in Settings (default every 4 hours, kept 90 days; older rows are removed nightly). It only runs the same read-only `show` commands as the Port Locator, a few switches at a time; a switch that times out is skipped and logged. Users with write access can start a sweep from the page. The Port Locator also shows a "Seen before" list of a MAC's last locations.
- **Automated tests**: a pytest suite (`tests/`, 243 tests) covering sign-in and access rules, themes, Port Locator parsing and tracing, config diffs, backups, change alerts, MAC/IP history and every page loading. Install with `pip install -r requirements-dev.txt` and run `python -m pytest`. A guard test fails if the package ever gains a real import-time cycle.

### Changed
- **IIS: live progress no longer held back**: `deploy/iis/web.config` turns off dynamic compression under `/api/operations/`, so progress lines reach the browser as they happen. `docs/WINDOWS-SERVER.md` section 6.6 covers the matching ARR setting (`responseBufferLimit=0`). Copy the new `web.config` to the site folder and run `iisreset` after updating.

### Fixed
- **Racks and IPAM summary boxes**: the KPI strip at the top of these pages lost its layout because its styles lived only on the VLAN audit page; they now come from the shared stylesheet.
- **Alert emails can never disturb a backup run**: a problem building or sending a config-changed or backup-failed email is now logged (Logs, category `alert`) and the run carries on, including the GitHub sync step that follows it.

## [1.18.2] - 2026-11-07

### Fixed
- **Port Locator shows live progress behind IIS**: an IP or MAC trace sat on "Tracing '…'" and then jumped straight to the result after 20-40 seconds, because the reverse proxy held the live progress stream back until the trace ended. While a trace runs, the page now also checks the trace's status once a second, so the bar, the current step (e.g. "Following LLDP, hop 1: …"), the switch count and the log lines update as the trace runs. Log lines are never shown twice and the bar never moves backwards when the held-back stream arrives.
- The "Starting trace…" line was printed twice in the trace log.

## [1.18.1] - 2026-11-07

### Fixed
- **Date picker grid**: dates no longer drift out of line with the weekday headers, and months that start late in the week (e.g. January 2027) no longer spill past the right edge. The blank days before the 1st picked up the app's empty-state padding, which widened their columns. The seven columns are now always equal width and line up with the headers.

## [1.18.0] - 2026-11-07

### Added
- **Glass / Normal and Day / Night switches**, remembered per user. Choose the look (Glass or Normal solid panels) and the mode (Dark or Light) in the top bar; your choice follows you to other browsers.
- **Liquid glass redone**: aurora (dark) and daylight (light) backgrounds, clear glass panels with light-catching edges, and a floating menu bar with a glass lens on the current page. Dropdowns, dialogs and the locator dock use stronger blur.
- **Status as dot + text** and **softer badges and buttons** (no outlines) across all pages.

### Changed
- **Back link moved next to the page title**: the menu bar no longer gains a Back button off the Dashboard, so it stays the same on every page and nothing shifts. Detail pages show a small link above the title that goes to their parent page:
  - device page → Devices; edit device → that device; add device → Devices;
  - backup view and compare → Backups.

### Security
- The theme cookie is marked `Secure` on HTTPS (it follows the session cookie setting and the reverse proxy's `X-Forwarded-Proto`).
- The theme API rejects a malformed request body with 400 instead of failing with 500.

### Fixed
- Text contrast in all four looks: selected calendar day, blue badges, role avatars on the Users page, and the sticky table header over scrolling rows in glass now meet WCAG AA.
- The Port Locator dock showed no colour on error because it used an undefined colour token.

## [1.17.1] - 2026-11-06

### Added
- **Universal Rounded Liquid Glass Dropdown Menus**: Replaced raw OS-native `<select>` dropdowns across all pages with the ObaidQatan Liquid Glass design system.
  - Consistent frosted backdrop blur (`blur(40px)`), 16px rounded popover corners, specular highlights, glowing cyan border accents, and custom checkmark indicators across the entire web interface.
  - Applied across **Port Locator** (Campus Site, Group, Location, Scope), **Hardware Health** (Campus Site, Health Status), **Optical Health** (Campus Site, Switch, Status), **VLAN & Port Audit** (Campus Site, Switch Filter, Port Mode, Status), **IPAM & Subnets**, **Rack Elevations**, **Lifecycle & EOL**, **Compare**, **Schedules**, **Settings**, **Device Modals**, **User Account Administration**, and the **Top Navigation Site Switcher Pill**.
  - **Adaptive Trigger Sizing**: Automatically respects `.input-lg` (44px height, 12px radius) and `.input` (38px height) form field heights, full-width container widths, and compact navbar pill layouts.
  - **Live Search Filtering**: Automatically activates for dropdowns with more than 6 choices with keyboard-accessible navigation and auto-selection on Enter.
  - **Dynamic Mutation & Scope Synchronization**: Seamlessly updates when choices are narrowed or replaced dynamically by JavaScript (e.g. Port Locator's hierarchical site/group/location filtering and VLAN Audit's switch population) while keeping narrowed-out options hidden.
  - **DOM Injection Auto-Enhancement**: Mutation observer dynamically discovers and applies liquid glass styling to new `<select>` elements created in modals and tabs without manual bindings.

### Changed
- **Progress bars follow each switch's steps** in Hardware Health, Optical Health and the VLAN audit live port probe. Each switch reports connecting, each command (e.g. "[CORE-SW1] Running 'show chassis environment'…") and parsing, and the bar adds these up across switches. The bar used to move only when a whole switch finished, so a scan of one or a few switches sat at 0% until the result. The bar never goes back and holds at 99% until the scan completes.

## [1.17.0] - 2026-11-06

### Added
- **Port Locator traces like you would by hand**: it looks the MAC up on the site's distribution and core switches, then follows the port it is learned on to the next switch using LLDP (bundle `ae` ports included), until it reaches a normal access port. A typical search now talks to 2-3 switches instead of every switch in the site, and the result shows the path, e.g. `DIST-EX3300-VC · ae12 → 10F-RACK1-ACC-SW3 · ge-0/0/14`.
  - Switches are recognised by their real hostname (read from `show version`, which often differs from the name in the Vault), by management IP, by the IP at the end of the LLDP name (`...-S1-S2-90.15`), or by a Vault name the LLDP name starts with.
  - A port with a known switch behind it on LLDP is followed, even when it looks like an access port (a single-cable uplink), so the answer is the real edge port and not an uplink.
  - Port descriptions that overflow the `show lldp neighbors` columns (e.g. `UPLINK_TO_DISTRIBUTION`) no longer break the trace. Port-channels on Cisco switches are followed through a member port.
  - The IP behind a MAC is looked up on the core in the background and no longer holds up the trace.
  - The progress bar follows the trace: "Looking up the MAC on 2 distribution/core switches", then "Following LLDP, hop 1: DIST-EX3300-VC ae11 → 4F-RACK2-ACC-VC2", with the number of switches queried so far. It used to sit at 0% until the result.
  - If the trace can't continue, for example no LLDP neighbour on the port or a neighbour that isn't in the Vault, the log says why and the locator falls back to scanning.
- **Group and Location filters** on the Port Locator. Choosing a site narrows the groups, choosing a group narrows the locations, and the switch list follows.
- **Stop early, "Show full path"**: a fallback scan stops at the first access-port hit and says how many switches it checked. **Show full path** scans every switch in scope and lists every port that has seen the device.

### Fixed
- **Port Locator speed**: when a switch didn't have the device, its empty answer was treated as a failure and the switch's whole MAC table (or ARP table) was downloaded and thrown away. That happened on nearly every switch, on every search. Empty answers are now trusted.
- **IP searches with a narrow scope**: the IP was only resolved on core switches inside the chosen scope, so picking one floor gave "IP not found". IPs are now always resolved on the site's distribution and core switches.
- **Port Locator Cancel** now stops the search; before, it only changed the status while every switch was still queried.
- **The firewall is no longer queried** by the Port Locator. It was sent switch commands it doesn't understand.
- Up to 16 switches are queried at once by the Port Locator (was the backup setting, 8).
- **Port Locator on Cisco switches**: a rejected command (`% Invalid input`) was read as "MAC not here". The locator now tries the next command form.
- **Port Locator**: a VLAN named like a port (e.g. `Telephony`) was shown as the interface.

## [1.16.0] - 2026-11-06

### Security
- **Juniper secrets were not masked in the GitHub copy**: the masking rule for Junos `$9$`/`$1$`/`$5$`/`$6$` secrets (RADIUS/TACACS keys, local user passwords, authentication keys) never matched, so with GitHub sync and "Mask secrets" on these were still pushed in clear. Fixed; the next backup of each Juniper switch pushes a masked copy. Earlier commits in the repository's history still contain them - rotate those secrets or rewrite the repository history.
- **Config-changed emails never contain secrets**: diff lines are masked the same way before they are emailed, and very long lines are cut at 300 characters.

### Added
- **Alerts page** (Admin → Alerts, Group Administrators only): one place for every email alert, separate from the mail-server settings.
  - **Three triggers**, each with an on/off switch and its own settings: Switch down / recovered (moved here from Settings, current values kept), **Backup failed** and **Config changed**.
  - **Messages**: each trigger has one or more messages. A message chooses who receives it (Group admins, Site admins for their own sites, extra addresses), can be limited to some sites, and has its own wording. Everyone gets each switch once per run, even when several messages include them.
  - **Wording editor**: subject, opening paragraph and closing line with clickable fields such as `{count}`, `{first_switch}`, `{site}` and `{committed_by}`; live preview with sample data, "Send test to me" and "Reset wording to default". The list of switches, errors and diffs is added automatically, so an edit can't break the email.
  - **Email style**: header colour, MGM logo (embedded, so it shows without loading remote images) and footer text, shared by every alert.
  - **Recent alerts**: the last 20 alerts sent or failed. Alerts are also in Logs under the new `alert` category.
- **Backup failed alert**: one email per backup run listing the switches whose backup failed and why. A switch is reported once until its backup succeeds again, so a dead switch isn't emailed every night.
- **Config changed alert**: one email per backup run listing switches whose configuration changed, with +/- line counts, who committed the change and how (Juniper), the first 40 changed lines (adjustable, 0 for none) and a "View full comparison" link. Changes found by manual backups can be included or left out.

### Changed
- Switch-down emails use the new shared style; when one check has switches going down and others recovering, each person gets a separate "down" and "recovered" email.
- Settings → Email Notifications now only holds the mail server settings, with a link to the Alerts page.

## [1.15.0] - 2026-11-06

### Added
- **Switch-down email alerts**: when a switch fails 2 reachability checks in a row (no ping and no SSH, about 10 minutes at the default 5-minute interval), an email goes out. A second email follows when it recovers, with how long it was down.
  - Group Administrators are told about every switch; Site Administrators only about switches in their sites. Users need an email address on their account.
  - Switches that go down in the same check are listed in one email per person, so a building outage is one message.
  - Short blips are ignored: in the last 30 days, 82% of "down" events were back by the next check.
  - Only the scheduled checks count. Pressing "Check" on a red switch updates its status but doesn't bring the email forward; a manual check that finds it back up does send the recovery email.
  - After upgrading, any switch that is already down is reported at the next scheduled checks, in one email per person.
  - Settings → Switch-down Alerts turns this on or off and sets how many failed checks trigger it (Group Administrators only). Outbound email must be enabled. Every alert sent, and any delivery failure, is recorded in Logs under Reachability.

### Changed
- **Instant menu navigation**: removed the 140 ms fade-out delay before every page change and the fade-in after it. Pages now switch with the browser's native cross-fade (Chrome/Edge; the top bar stays still) and other browsers switch instantly. Turned off when Windows is set to reduce motion.
- **Menu pages start loading on press**: pressing a menu link starts fetching the page before the click completes (Chrome/Edge). Only menu links are fetched early; Logout, downloads and exports never are. Hovering fetches nothing, so a page is never shown stale.
- **Static file caching when running without IIS**: versioned CSS and JavaScript are cached until the next release and images for a day. Behind IIS, `deploy/iis/web.config` already caches them for 7 days and its headers take precedence, so nothing changes there.

### Fixed
- **IPAM and Rack Elevations pages**: a duplicate script variable stopped the shared app script from loading on these two pages. The shared `closeModal` and `escapeHtml` now also accept the way these pages call them (closing a named modal; empty values), which also stops Hardware Health and the device pages throwing on empty fields.

## [1.14.0] - 2026-11-06

### Security
- **Role checks fail closed**: `require_write`, `require_admin` and `require_group_admin` now refuse requests when the user record cannot be found, instead of letting them through.
- **Sessions follow the account**: every request re-checks the signed-in user. Deleting a user ends their sessions immediately, and a password change or reset signs out the account's other sessions (the session that made the change stays signed in).
- **Group Administrator only**:
  - Export & migrate (`/migrate*`: full export with encryption keys, restore, restart, app package).
  - Software update check and install.
  - GitHub sync settings, test, sync, push and re-download (any user can still view the page).
  - Log purge.
  - SMTP and *Application Public Web URL* settings, and the test-email button. Site administrators keep backup, retention, SSH and log settings.
- **Device credentials can no longer be redirected**:
  - CSV import requires write access, imports into a site the user can access, and skips devices that belong to other sites.
  - *Backup*, *Check reachability* and *Detect model* check the user's role and site access for every selected device.
  - A device cannot be linked to a credential profile from a site the user has no access to.
- **Site-scoped backups**: viewing, downloading, comparing and ZIP downloads only include backups from the user's sites. Pinning and deleting backups require write access.
- **Write access required** for saving, running, toggling and deleting schedules and for deleting credential profiles.
- **User management**: site administrators can no longer delete, demote or reset the password of group administrators, or of users who also belong to sites outside their own.
- **Password reset links** are built only from the configured *Application Public Web URL*, never from the request's Host / X-Forwarded-Host header. Reset emails are not sent while it is blank.
- **First-login lockdown**: while a password change is pending, the Users page only accepts the password change.
- CSRF tokens are compared in constant time.

### Fixed
- **Lifecycle "Fetch all"** never contacted a switch: worker threads read the login session outside the request, and `get_int` was not imported.
- **Restarts no longer undo edits**: the one-off start-up fixes (Malar devices and credential to MGM-Malar, Malar Cisco models, `Floor-N` to `Level-N` groups, default sites) now run once and are recorded in the settings table.
- **Missing database indexes** declared in the models (e.g. `device.site_id`, `credential.site_id`) are created at start-up on older databases.
- **Downloads blanked the page**: the page fade-out no longer runs for CSV, ZIP, `.diff`, config and package downloads, and pages restored with the browser's Back button are no longer faded out.
- **Duplicate backup jobs**: *Backup now* / *Backup selected* stay disabled until the job finishes.
- **Stuck progress toast**: job polling stops when the session expires, the job is missing or the server is unreachable for about a minute.
- *Check reachability* with nothing selected now asks for a selection instead of reporting "0/0 reachable".

### Changed
- **Smoother Liquid Glass UI**:
  - Background is now static, fixed layers: no infinite animated 80px blur and no `background-attachment: fixed`, which repainted every glass panel on each frame and on scroll.
  - Page load is a short fade and slide without a page-wide blur filter or a permanent `will-change` layer; page change delay reduced from 220 ms to 140 ms.
  - Backdrop blur kept on the navigation bar, cards, modals and popovers, removed from buttons, badges, table headers, inputs and filter bars.
  - Cards no longer lift on hover (removes the jitter when moving the pointer over tables); buttons no longer scale on hover.
  - Dropdown and calendar popovers reposition at most once per animation frame while scrolling.
- **Faster pages**:
  - Devices: site and credential loaded up front and backup counts in one grouped query (88 to 18 database queries).
  - Dashboard: 14-day chart from one grouped query instead of 28 counts (122 to 45 queries).
  - Backups: previous-backup lookup for all rows in one query (143 to 47 queries).

### Upgrade notes
- Every user is signed out once after the upgrade.
- Set *Settings → Application Public Web URL* before relying on *Forgot password*.

## [1.13.7] - 2026-11-05

### Fixed
- **Credentials & Multi-Site Access (HTTP 500 Error)**:
  - Resolved `TypeError: 'set' object is not subscriptable` in `_active_site_id()` and credential/schedule views when accessed by site administrators or non-group-admin users.
  - Standardized `User.allowed_site_ids` to return a deterministic sorted list of site IDs instead of an un-subscriptable set.
  - Added robust fallback `device_count` property to `Credential` model, ensuring profile tables render without error regardless of SQLAlchemy relationship loader strategy.
- **Dynamic Viewport Responsiveness on 1366x768 Displays**:
  - Prevented page-level horizontal overflow (`html, body { max-width: 100%; overflow-x: hidden; }`).
  - Added dedicated `@media (max-width: 1400px)` dynamic layout scaling for 1366x768 laptop and desktop monitors across all core tables.
  - Compacted Devices table columns, padding, and filter inputs so all 13 columns fit within 1249px available width without requiring horizontal scrollbars.
  - Truncated `Last commit` column on Backups table with ellipsis and native tooltip to preserve horizontal table boundaries.
  - Optimized Users & Access Management page (`/account`) with adaptive 270px sidebar and compact user action groups on screens $\le$ 1400px.
- **Cleaned Devices Table Layout**:
  - Removed duplicate `legacy` badge from device names in the main inventory table to save space, keeping legacy SSH details accessible via device detail and link tooltips.

## [1.13.6] - 2026-11-05

### Added
- **Liquid Glass Rounded Calendar Date Picker**:
  - Implemented custom Liquid Glass Datepicker component (LiquidGlassCalendar) with rounded 20px corners, live blur (backdrop-filter: blur(40px)), and specular reflections.
  - Replaced browser/OS native square picker dialog with smooth floating frosted calendar dialog supporting month/year navigation, quick 'Today' and 'Clear' buttons, and today/selected day indicators.

### Fixed
- **Dropdown & Popover Stacking Context (Never Goes Behind Headers)**:
  - Portaled all floating select popovers and calendar dialogs directly to document.body with fixed viewport positioning and z-index: 999999.
  - Resolved issue where select dropdown menus were slipping behind sticky table headers (th), action buttons (Backup selected, Compare 2 selected), or being clipped by card boundaries.
  - Added dynamic viewport bounds checking and auto-flip upward when near screen bottom.

## [1.13.5] - 2026-11-04

### Added
- **ObaidQatan Liquid Glass Component Library System**:
  - Re-architected glassmorphism effects across all pages based on [ObaidQatan/liquid-glass-component-library](https://github.com/ObaidQatan/liquid-glass-component-library).
  - Ambient refractive fluid glow orbs in the background driving continuous live light refraction through frosted glass surfaces.
  - Multi-stop specular glass gradients on panels, cards, modals, and nav dock (linear-gradient(135deg, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0.05) 50%, rgba(255, 255, 255, 0.02) 100%)).
  - Top highlight sheen lines (linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.45), transparent)) along the top border of cards, navigation bars, dropdowns, and modals.
  - Dual-tier rim reflections with specular top edge and bottom shadow rim.
  - Apple VisionOS fluid spring transitions (cubic-bezier(0.16, 1, 0.3, 1)) for page load entrances, card reveals, dropdown menus, and modal dialogs.
  - Custom Liquid Glass Select component (.lg-select) providing floating frosted glass dropdowns with search filtering, active selection indicators, and smooth spring animations.
  - Frosted glass modal backdrops (backdrop-filter: blur(24px) saturate(190%)) with spring scaling entrances (modal-liquid-enter).

## [1.13.4] - 2026-11-04

### Fixed
- **Date Selector & Calendar Popup Styling**:
  - Added `<meta name="color-scheme" content="dark">` to `base.html` and `color-scheme: dark;` to `:root`, `html`, and `input[type="date"]`.
  - Fixed native browser calendar dialog appearing in bright white by forcing the browser to render the calendar in native dark mode matching the Liquid Glass studio palette.
  - Styled `-webkit-calendar-picker-indicator` with luminous cyan glow and invert filters.
- **Dropdown & Filter Menus Liquid Glass Styling**:
  - Restyled all `<select>` inputs with dark translucent glass backgrounds (`rgba(9, 15, 30, 0.68)`), frosted glass blur, specular rim highlights, and glowing cyan SVG chevrons.
  - Replaced native grey dropdown menus with dark midnight obsidian (`#0b1426`) option styling.
- **Top Navigation Back Button Vertical Alignment**:
  - Removed legacy `margin-bottom: 14px` rule on `.back-btn` that was pushing the button upward out of alignment.
  - Standardized `.back-btn`, `.nav-brand`, and `.nav-item` to identical 32px heights and vertical flex alignment for pixel-perfect linear centering.

## [1.13.3] - 2026-11-04

### Fixed
- **Backup Retention & Disappearing Backups**:
  - Configured explicit descending ordering (`order_by='desc(Backup.created_at)'`) on the `Device.backups` relationship and retention pruning queries.
  - Resolved an inverted retention issue where devices that reached the retention limit (e.g. 20 backups) were purging newly taken backups instead of the oldest backups.
  - Ensured `prev = device.backups.order_by(Backup.created_at.desc()).first()` correctly retrieves the most recent backup baseline for config change diffing.

### Added
- **Configurable Backup Local Directory**:
  - Added dedicated `backup_dir` setting in `config.toml` (`D:/NetworkVault/Config-Backups`) and `network_vault/config.py`.
  - Migrated all 3,359 existing stored device configuration archives seamlessly from `C:\MGM\mgm-network-vault\data\backups` to `D:\NetworkVault\Config-Backups`.
  - Updated storage telemetry and backup exports to operate directly with the custom backup location.

## [1.13.2] - 2026-11-04

### Added
- **Liquid Glass Studio UI Theme & Ambient Backdrop**:
  - Implemented modern visionOS / Apple-inspired **Liquid Glass Studio** interface styling across the entire suite.
  - Added illuminated neon teal and deep cyan keyboard ambient backdrop (bg-liquid.jpg) with radial lighting and high-contrast dark frosted overlay.
  - Multi-pass frosted glass cards and panels with backdrop-filter: blur(28px) saturate(190%) and specular rim refraction highlights (box-shadow: inset 0 1px 0 rgba(255,255,255,0.18)).
  - Translucent floating top dock and contextual sub-navigation bars with glowing cyan pill tabs.
  - Liquid glass capsule badges with neon glows and gradient primary action buttons.
  - Upgraded dashboard Chart.js telemetry: luminous cyan backup bars, electric gold config change curves, and neon reachability donut segments.
  - Deep frosted glass floating operation progress modals and port locator dock widgets.

## [1.13.1] - 2026-11-04

### Fixed
- **Backup Engine & Nightly Scheduler Execution**:
  - Restored essential columns on the `Job` model (`trigger`, `started`, `finished`, `total`, `done`, `ok`, `failed`, `unchanged`, `status`) aligning with the existing SQLite schema.
  - Resolved `TypeError: 'trigger' is an invalid keyword argument for Job` causing **HTTP 500** during manual backups (`/api/backup`).
  - Restored automated execution of scheduled nightly backups (`Schedule #1 (Nightly backup)`) and syslog-triggered backups.
- **Admin Settings & User Authorization Access**:
  - Restored missing `User.is_admin`, `User.can_write`, `User.can_backup`, and `User.allowed_site_ids` properties in `network_vault/models.py`.
  - Resolved `AttributeError: 'User' object has no attribute 'is_admin'` causing **HTTP 500** on `/settings`.
- **Credential Profiles Directory & Safe Deletion**:
  - Reverted `Credential.devices` relationship mapping from `dynamic` to standard eager/lazy list and added robust count fallbacks in `credentials.html` and `views.py`.
  - Resolved `TypeError: object of type 'AppenderQuery' has no len()` causing **HTTP 500** on `/credentials` and credential deletion.
- **VLAN Port Audit & Transceiver Device Properties**:
  - Restored `Device.is_juniper`, `Device.is_sonicwall`, and `Device.model_label` properties in `models.py`.
  - Resolved `AttributeError: 'Device' object has no attribute 'is_juniper'` during VLAN port audit background probes (`/api/operations/vlan-audit/data`).
- **Backup Diffs & Comparison History**:
  - Restored `Backup.previous` property in `models.py` for config comparison links and delta calculation across dashboard, device views, and backup archives.
- **Top Bar Title & Navigation Mojibake Glitches**:
  - Replaced corrupted double-encoded UTF-8 characters (`Ã,Â·`, `Ã‚Â·`, `Ã¢Å“â€¢`, `Ã¢â‚¬Â¦`) in `base.html` with fail-safe standard HTML entities (`&middot;`, `&times;`, `&hellip;`).
- **Dependencies & Imports**:
  - Added missing `import json` in `models.py` for Virtual Chassis member and job details serialization.

## [1.13.0] - 2026-10-31

### Added
- **IPAM Module (IP Address Management & Subnet Hierarchies)**:
  - **Subnet Hierarchy Trees & Visual Utilization (`/ipam`)**:
    - Multi-level nested tree containment for IPv4 and IPv6 subnets with dynamic, color-coded utilization progress bars (Green <60%, Amber 60-85%, Red >85%).
    - Supports container supernets, allocated counts, broadcast, and netmask calculations via `netaddr`.
  - **Allocated IP Address Directory**:
    - Host IP tracking with DNS hostnames, subnet prefix bindings, device interface bindings, and VIP roles.
  - **VLANs & QinQ Dual-Tagging**:
    - 802.1Q standard VLANs (1–4094), status, roles, VLAN Groups, and 802.1ad QinQ dual-tagging (Outer `S-VLAN` and Inner `C-VLAN` mapping).
  - **VRF Routing Instances**:
    - Virtual Routing and Forwarding tables with Route Distinguisher (RD) tracking, prefix counts, and tenant IP isolation.
  - **FHRP Redundancy Groups**:
    - VRRP, HSRP, and GLBP redundancy clusters with Virtual IPs, authentication types, and member switch priority rankings.
  - **Full REST API & Interactive Modals**:
    - Complete CRUD endpoints under `/api/ipam/*` with CSRF protection and toast feedback.

- **DCIM Visual Rack Elevations & Equipment Cabinets**:
  - **Interactive 42U/48U Cabinet Graphic (`/racks`)**:
    - High-fidelity visual rack elevation with metal side rails, embossed unit numbering (U42 ... U1), and Front vs. Rear elevation toggle.
    - Switch faceplates styled with real-time status LED indicators (Up, Down, Warning), hardware model badges, management IP pills, and role gradients (Core, Distribution, Access, Firewall, Server).
    - Multi-RU equipment spanning (e.g. 2U switches cleanly span two rack units).
    - Hover inspection tooltips with direct jump links to device backups and configuration diffs.
  - **Rack Space Management**:
    - Real-time space utilization metrics (Occupied RUs, Reserved RUs, Available RUs, and Utilization %).
    - Equipment Mounting & Unmounting directly from available inventory.
    - RU space reservations for planned future hardware with user notes.
    - Cabinet organization by Site and Room / IDF Closets.
  - **Pre-Populated Cabinets**:
    - Automatically organized 15 existing inventory switches into physical cabinets (`MDF-CORE-RACK-01`, `1F-RACK1`, `1F-RACK2`, `GF-RACK1`, `UTILITY-RACK1`, `4F-RACK1`, `2F-LAB-RACK`).

### Fixed
- **Schedule & LogEntry ORM Schemas**: Added missing database model columns (`frequency`, `time`, `day_of_week`, `day_of_month` to `Schedule`, and `device_name`, `details` to `LogEntry`) to align with existing SQLite schema.
- **Device & Backup ORM Properties**: Added `vc_degraded` property to `Device` and restored missing `Backup` model columns (`changed`, `lines_removed`, `sha256`, `path`, etc.) resolving dashboard calculation exceptions.

## [1.12.3] - 2026-10-27

### Added & Optimized
- **Complete Mobile & Tablet Browser Optimization**:
  - **Slide-out Mobile Navigation Drawer (`#mobileNavDrawer`)**:
    - Added a hamburger toggle button (`#mobileMenuBtn`) on tablets and mobile screens (<= 900px), opening an ergonomic, full-featured touch drawer with MGM brand lockup, active site selector, primary links, operations suite, admin controls, and user sign-out.
    - Added backdrop blur overlay (`.mobile-nav-backdrop`) with tap-outside and Escape dismiss behavior.
    - Integrated background scroll locking when the mobile drawer is open to prevent background viewport drift.
  - **Mobile Touch Layout & Sizing**:
    - Adjusted `.main.full-width` padding on small screens (12px on mobile vs 28px on desktop) to maximize usable screen real estate.
    - Added touch-friendly minimum tap targets (>= 38px) across all buttons, filter controls, and action triggers.
    - Set `input, select, textarea { font-size: 16px !important; }` on mobile (< 680px) to prevent iOS Safari from automatically zooming into the page when tapping form controls.
  - **Responsive Stats & Grid Stacking**:
    - Stat cards (`.stats`) automatically adapt into a 2-column grid on phones and single-column on ultra-narrow screens (<= 380px) instead of compressing.
    - Dashboard overview grids (`.dash-overview-grid`), activity charts, reachability donuts, and 2/3-column grids stack vertically into single-column cards on smaller viewports.
  - **Full-Width Table Touch Scrolling**:
    - Added smooth touch horizontal scrolling (`overflow-x: auto; -webkit-overflow-scrolling: touch`) across all table views, switch inventories, and diagnostics matrices, preventing wide tables from breaking horizontal page boundaries on mobile devices.
  - **Mobile Dialogs & Docks**:
    - Responsive modal dialogs (`.modal`) with touch padding and max-height scrolling.
    - Mobile-optimized bottom-anchored floating Port Locator dock (`.global-locator-dock`).

## [1.12.2] - 2026-10-27

### Fixed
- **Top Navigation Bar Spacing & Scaling**:
  - Fixed horizontal flex layout to prevent the `Admin ▾` menu and `Site Switcher` dropdown from overlapping at 100% display scaling and standard laptop display widths.
  - Made `.top-nav-links` `flex: 0 0 auto` and aligned `.top-nav-right` with `margin-left: auto; flex-shrink: 0`, preventing flexbox from artificially shrinking the navigation links box and causing invisible overlap.
  - Streamlined brand lockup, pill padding, and site selector text clipping (`text-overflow: ellipsis`) to maintain generous horizontal breathing room across all desktop resolutions.
  - Added responsive media queries (at 1280px and 1120px) to gracefully adapt navigation items without horizontal clipping.
- **Dropdown Toggle & Collapse Behavior**:
  - Removed persistent CSS `:hover` and `:focus-within` rules from `.nav-dropdown-menu`, ensuring `Operations ▾` and `Admin ▾` menus toggle open/closed cleanly when clicked repeatedly.
  - Added button focus blur and `aria-expanded` state tracking to prevent lingering focus states.
  - Added Escape key dismiss handler to quickly close active dropdowns.

## [1.12.1] - 2026-10-27

### Optimized & Redesigned
- **Top Navigation Bar & Full-Width View Layout**:
  - Replaced the vertical left sidebar with a sleek, sticky full-width **Top Navigation Bar** (`.top-nav-bar`), freeing up 100% horizontal screen space for tables, switch inventories, and diagnostics.
  - Organized all 16 menus into an intuitive top navigation header:
    - **Primary Tabs**: Dashboard, Devices, Backups, Schedules, Logs.
    - **Operations ▾ Dropdown**: Port Locator, Hardware Health, Optical Health, VLAN & Port Audit, Lifecycle & EOL.
    - **Admin ▾ Dropdown**: Sites, Credentials, GitHub sync, Export & migrate, Settings, Users.
    - **Contextual Sub-Nav Strip**: Fast 1-click switcher appears beneath the top navbar when navigating inside Operations or Admin tools.
    - **Top Bar Utility Group**: Brand logo lockup, Back button, Site Switcher capsule, User Profile pill, and version indicator.
  - **Full-Width Screen Utilization**: Expanded `.main` to 100% full viewport width (`.main.full-width`), completely eliminating horizontal compression and awkward viewport scrolling on table views.
  - **Devices Table Optimization**: Refined column distribution on `.devices-table` with snug adjacent spacing between Name and Host, allowing Group / Location and notes to absorb wide screen real estate.

## [1.12.0] - 2026-10-26

### Added
- **Fleet Hardware Health & Telemetry Dashboard (`/operations/health`)**:
  - Live hardware monitoring suite tracking CPU utilization, memory pressure, chassis/CPU thermal sensors, and fan / power supply environmental metrics across campus infrastructure.
  - Multi-platform hardware support across Juniper Junos (`show chassis routing-engine`, `show chassis environment`), Cisco IOS (`show processes cpu`, `show processes memory`, `show env all`), Cisco Catalyst 1300 / CBS (`show cpu utilization`, `show environment all`), and SonicWall SonicOS (`show status`).
  - **Live SSH Scanning Engine (`/api/operations/health/stream`)**: Parallel multi-threaded interrogation with Server-Sent Events (SSE) streaming progress bar, stats counter, and live terminal console.
  - **Single Switch Health Check (`/api/devices/<id>/health`)**: On-demand single switch check button in the table for immediate diagnostics.
  - **KPI Summary Strip**: Interactive top cards for Monitored Switches, Normal/Healthy, Warning/Elevated, and Critical/Fault with one-click filtering.
  - **Interactive Sorting**: Bi-directional sortable column headers (`▲` / `▼`) for Switch Hostname, Campus Site, CPU Utilization, Memory Usage, and Temperature.
  - Sidebar navigation link with activity/pulse icon under Operations.

- **Lifecycle & EOL Live Inventory Fetch & Extended Catalog**:
  - Added **`Fetch Live Inventory & EOL`** live scan (`/api/operations/lifecycle/fetch`) and individual **`Fetch`** buttons per switch (`/api/devices/<id>/discover`), polling `show version` & `show inventory` live over SSH to detect exact hardware PID, serial number, and running software.
  - Added official Cisco EOS & EOL milestones for:
    - **Cisco Catalyst 1000 Series** (`C1000-24T-4X-L`, `C1000-48T-4X-L`): End-of-Sale 2025-04-30, Last Date of Support (EOL) 2030-04-30, Recommended Release `15.2(7)E3k`.
    - **Cisco Catalyst 9300 Series** (`C9300-24T`): Active flagship enterprise campus switch, Recommended Release `17.9.4a`.

### Fixed
- **SQLAlchemy QueuePool Limit Exhaustion**:
  - Configured `poolclass: pool.NullPool` in `SQLALCHEMY_ENGINE_OPTIONS` for SQLite, resolving connection pool exhaustion (`QueuePool limit of size 5 overflow 10 reached`) during concurrent background tasks.
- **Port Locator ARP Discovery Optimization**:
  - Streamlined Step 1 of Port Locator to query only actual Core & Distribution switches (`MGM-CSW`, `MALAR-CSW`, `DIST-EX3300-VC`) instead of scanning all 68 edge switches, drastically accelerating ARP lookup.
- **CSRF Token Handling in SSE Streams**:
  - Included `X-CSRF-Token` headers on all streaming POST requests across Hardware Health and Lifecycle Fetch endpoints.

## [1.11.1] - 2026-10-19

### Added
- **User Last Login Tracking & Directory Display**:
  - Added `last_login_at` timestamp tracking to the `User` model, automatically recorded upon every successful authentication.
  - Added **Last Login** column to the User Directory table in **Users & Access Management** (`/account`), showing both formatted timestamp (`YYYY-MM-DD HH:MM`) and human-readable relative time (e.g. `2h ago`, `3d ago`, or `Never`).
  - Added **Last Login** display row in the current user's profile card for fast personal session tracking.
  - Automatic database schema migration with historical backfill populating initial login timestamps from existing `auth` audit logs.

## [1.11.0] - 2026-10-18

### Added
- **VLAN Matrix & Dormant / Unused Port Auditing**:
  - Dedicated network operations audit suite (`/operations/vlan-audit`) analyzing cross-switch VLAN allocations, port capacity utilization, and reclamation of dormant / unused switchports.
  - **Port Capacity & Dormant Interface Inventory**:
    - Complete switchport catalog across all campus switches (3,400+ ports) with instant filtering by site scope, specific switch, port mode (Access vs Trunk), operational status (Connected vs Dormant vs Admin Down), and real-time search.
    - Badging for Access vs Trunk modes, VLAN IDs with primary network names, admin status (UP vs DOWN), operational link status, and uplink vs access drop roles.
  - **VLAN Matrix & Cross-Switch Grid**:
    - Interactive matrix displaying campus-wide VLAN definitions across all switches.
    - Automated detection of cross-switch VLAN naming conflicts and inconsistencies.
    - Visual indicators for switches where a VLAN is deployed as Access, Trunk, or both.
  - **Switch Utilization Overview**:
    - High-level capacity breakdown per switch: total configured ports, active/connected ports, dormant ports, disabled ports, and visual capacity utilization progress bars.
  - **Live Hardware Interrogation (SSH Stream)**:
    - Real-time parallel SSH link-state probe (`/api/operations/vlan-audit/stream`) testing hardware operational link states directly on physical switches.
    - Streaming progress bar, live metrics counter, and terminal log console with auto-scroll and copy-to-clipboard support.
    - Top-aligned console modal with auto-scroll to top upon launching live audits.
    - Persistent cache (`data/live_ports_cache.json`) saving live hardware link states on disk so results persist across browser reloads.
  - **CSV Inventory Export**:
    - One-click CSV export (`/operations/vlan-audit/export.csv`) of complete campus switchport inventory with site, role, interface, description, mode, VLAN assignment, and operational status.

### Fixed
- **JunOS Aggregated Ethernet (LAG) & ELS Trunking**:
  - Implemented LAG bundle tracking (`802.3ad ae<id>`) resolving physical member ports to their parent bundle's trunk mode and VLAN tags.
  - Added support for JunOS Enhanced Layer 2 Software (ELS) `interface-mode trunk` syntax alongside legacy `port-mode`.
  - Added intelligent uplink keyword detection (`uplink`, `trunk`, `sw-to-sw`, `dist`, `core`) guaranteeing uplinks are recognized as Trunk mode.
- **Cisco C1300 / CBS & IOS Multi-format Live Port Parsing**:
  - Resolved an issue where Cisco C1300 / CBS switches reported all ports as connected due to `Up` / `Down` state token formatting.
  - Standardized interface normalization (`Gi1`, `Te1`, `Po1`) and case-insensitive resolution across live probe and backup configurations.
  - Corrected offline baseline calculation to keep unlabeled access drops marked as dormant rather than assuming active connectivity.

### Infrastructure
- **IIS HTTP/2, Dynamic Compression, & Caching**:
  - Enabled HTTP/2 and HTTP/3 support, dynamic GZip compression, and static asset caching via reverse proxy `web.config`.
  - Added self-elevating service restart utility (`restart_vault.bat`).

## [1.10.2] - 2026-10-17

### Fixed
- **Email Input Field Styling & Theme Consistency**:
  - Added `input[type=email]` to the core stylesheet (`app.css`) form control rules, resolving an issue where email inputs defaulted to unstyled white browser backgrounds.
  - Styled corporate email inputs in both the **Add New User** form and **Edit Access** modal with sleek, minimal theme-matching background (`var(--bg)`), subtle borders (`var(--border)`), and soft gold accent focus rings.
  - Simplified and refined field labels and helper captions.

## [1.10.1] - 2026-10-17

### Changed
- **Branded Sender & Streamlined Password Reset Email**:
  - Configured outbound sender display name to **`MGM Networkvault`** (`MGM Networkvault <donotreply@mgmhealthcare.in>`) with RFC 5322 formatted address headers.
  - Added configurable **Sender Display Name** (`smtp_from_name`) field in **Settings** (`/settings`).
  - Removed the fallback raw URL paragraph ("If the button above does not work...") from password reset emails for a cleaner, modern appearance.
  - Upgraded reset email styling to bulletproof table and inline CSS buttons, ensuring consistent navy `#25366C` branding across web and desktop email clients.

## [1.10.0] - 2026-10-17

### Added
- **Self-Service "Forgot Password" with Outlook / Microsoft 365 Email Reset Links**:
  - Added a **"Forgot password?"** self-service recovery link directly on the sign-in screen (`/forgot-password`).
  - Users can request a reset link by providing either their username or registered corporate email address.
  - Generates secure, cryptographically signed, time-limited reset tokens (valid for 15 minutes) using `itsdangerous`.
  - Tokens are bound to the user's password hash suffix, automatically invalidating existing links once a password is changed.
  - Native **Outlook / Office 365 SMTP** integration (`smtp.office365.com:587` with STARTTLS) via `network_vault/mailer.py`.
  - Dispatches branded, responsive HTML reset emails with one-click "Reset My Password" buttons and fallback URLs.
  - Dedicated **Email Notifications & Password Reset (SMTP)** panel in **Settings** (`/settings`) with encrypted credential storage at rest and a live **"Send Test Email"** tool.
  - Extended user management (`/account`) with registered corporate email fields on user creation and profile editing forms.
  - Anti-enumeration protection ensuring uniform feedback messages regardless of whether an identity exists.

## [1.9.1] - 2026-10-17

### Added
- **User Page Redesign & Site-Admin Password Reset**:
  - Implemented an administrative option for **Site Administrators** (and Group Admins) to reset passwords for other users within their assigned campus sites (`action="resetpassword"`).
  - Built-in secure password generator with one-click clipboard copy and automated `must_change` flag forcing a permanent password update on next login.
  - Strict security boundaries: Site Admins cannot reset passwords for Group Admins or users outside their assigned campuses.
  - Complete modern redesign of the User & Access Management page:
    - Real-time search filter across usernames, roles, and campus codes.
    - User stats ribbon summarizing total users, group admins, site admins, and operators.
    - Redesigned user directory table with role-colored avatars, campus badges, and streamlined actions.
    - Dedicated profile card with live password strength indicator and show/hide password toggles.
    - Polished modals for user password resets and role/site editing.

## [1.9.0] - 2026-10-17

### Added
- **Per-Site Git Branch Backup Isolation**:
  - Configured each site (`MGMHC`, `MGM-Malar`, `MGM-CORP`, etc.) to automatically push configuration backups to its own dedicated branch on GitHub.
  - Multi-working-copy repository architecture (`data/git-repos/<site_code>/`) eliminating git lock contention and branch-switching race conditions during concurrent backup runs.
  - Granular per-site locking ensuring multi-threaded backup engines can write and commit configs across multiple sites in parallel.
  - Direct branch links in the UI:
    - **GitHub Status** page displays all initialized site branches with clickable links to their respective branch tree on GitHub.
    - **Device Detail** page links directly to the device's configuration history on its specific site branch.
  - Historical replay migrated all existing database backups into their corresponding site branches, deduplicated and pushed upstream with zero pending commits.

## [1.8.2] - 2026-10-16

### Added
- **Editable & Transferrable Credential Profiles Across Sites**:
  - Added an interactive **Site** selector to credential profile creation and edit forms (`/credentials`).
  - Administrators can now freely transfer credential profiles between sites (e.g., reassigning shared campus credentials like `Mgm-Malar-SW` to `MGM-Malar`).
  - Device edit forms now automatically offer credential profiles belonging to the target device's site or the current site context.

### Fixed
- **Malar Switch Platform & Driver Configuration**:
  - Corrected `MGM-MALAR-B-1ST-FLOOR`, `MGM-MALAR-B-5TH-IT`, `MGM-MALAR-B-4TH-FLOOR`, and `MGM-MALAR-B-5TH-FLOOR` from mismatched `juniper_junos` / `cisco_c1300` to native **`cisco_ios`** with their detected hardware models (**Cisco Catalyst C1000** and **Catalyst 2960-S**).
  - Resolved `ReadTimeout: Pattern not detected: 'Screen width set to' in output` caused by sending Junos terminal commands to Cisco switches.

## [1.8.1] - 2026-10-16

### Fixed
- **Cisco Switch SSH Transport & VTY Session Teardown**:
  - Fixed a connection leak in the `CiscoC1300SSH` driver where in-band authenticated SSH sessions (`_establish_inband_connection`) did not attach the underlying `paramiko.Transport` to `remote_conn_pre`. This caused sessions on Cisco C1300 / CBS / SG switches to remain lingering on the switch until its internal 10-minute timeout expired.
  - Implemented explicit `disconnect()` override ensuring the underlying Paramiko transport is terminated on teardown, preventing switch VTY session exhaustion (`Server reached maximum number of simultaneous connections`).
- **Clear Switch VTY Exhaustion Diagnostics**:
  - Enhanced connection error handling to detect when a Cisco switch closes the TCP connection due to full VTY slots (`Server reached maximum number of simultaneous connections`), reporting a clear, actionable message to administrators instead of the generic `Error reading SSH protocol banner`.

## [1.8.0] - 2026-10-15

### Added
- **SonicWall Next-Gen Firewall (SonicOS / NSa) Support**:
  - Full platform support for **SonicWall Firewalls** (`sonicwall_sonicos`) including the enterprise **SonicWall NSa 4800**.
  - Built-in `SonicWallSonicOSSSH` driver handling SonicOS terminal negotiation and paging (`no cli pager`).
  - Automated configuration backup collecting `show current-config` (with fallback to `export current-config cli`).
  - Fact discovery for SonicWall hardware model (`NSa 4800`), serial number, and firmware version (`SonicOS Enhanced 6.5.x`).
  - Syslog commit event detection intercepting configuration change alerts in real-time for instant event-driven vaulting.
  - Added **SonicWall NSa 4800 Series** to the Lifecycle & EOL catalog with milestone dates (EOS: March 2021, EOL: March 2026, Gold Standard: `SonicOS 6.5.4.15-116n`).

## [1.7.2] - 2026-10-15

### Fixed
- **Resilient SSH Handshake & Auto-Retry with Backoff**:
  - Implemented automatic retry mechanism with exponential backoff (`retries=2`, `backoff_base=3s`) for transient SSH connection drops.
  - Automatically recovers from `SSHException: Error reading SSH protocol banner`, socket EOF resets, and banner timeouts caused by switch VTY session pool exhaustion or temporary CPU spikes (frequent on Cisco Catalyst 1300 / CBS and older switches during bulk schedule runs).
  - Logs non-fatal warning events during retries while completing backups seamlessly without marking the job as failed.

## [1.7.1] - 2026-10-14

### Improved
- **Main Dashboard Layout & Lifecycle Overview**:
  - Re-architected dashboard overview section into a unified, responsive 3-column layout matching executive operations view:
    - **Column 1**: Full-height responsive Backup & Change Activity timeline chart.
    - **Column 2**: Compact Reachability donut chart stacked above the interactive Hardware Family Lifecycle distribution widget.
    - **Column 3**: Firmware & OS Standards breakdown showing campus builds and device distribution.
  - Added clickable redirects on overview cards directly into the full **Lifecycle & EOL** operations dashboard.
  - Removed redundant duplicate breakdown widgets from the `/operations/lifecycle` page for clean separation of concerns.
- **UI Aesthetics & Scrollbars**:
  - Implemented sleek, theme-adaptive custom scrollbars (`5px` width, translucent rounded thumbs) matching both dark and light palettes across all browsers.

### Added
- **Firmware & EOL / EOS Lifecycle Dashboard** (`/operations/lifecycle`):
  - Executive lifecycle KPI overview tracking **Active & Supported**, **Approaching EOL (< 12 months)**, and **End-of-Life / Past Support** hardware across the campus.
  - Built-in hardware lifecycle catalog for **Juniper Networks** (EX2200, EX2300, EX3300, EX4600) and **Cisco Systems** (Catalyst 1300, CBS 250/350, Catalyst 3750/3560, Catalyst 2960).
  - Tracks key OEM lifecycle milestones:
    - **End-of-Sale (EOS)**
    - **End-of-Software-Maintenance (EoSW)**
    - **End-of-Security-Vulnerability-Support (EoSec)**
    - **Last-Date-of-Support (EOL / LDOS)**
  - **Firmware Gold Standard Compliance**: Compares active software build against vendor TAC recommended releases, highlighting divergence and upgrade recommendations.
  - **Hardware Family Distribution**: Visual breakdown of campus switch generations and operating system fragmentation.
  - **Audit CSV Export**: One-click download of the full hardware and software lifecycle inventory report for network budgeting and executive presentations.
  - Integrated into the **Operations** sidebar navigation menu.

## [1.6.0] - 2026-10-13

### Added
- **Syslog-Triggered Instant Backups ("Event-Driven Vaulting")**:
  - Integrated asynchronous UDP Syslog listener daemon ([syslog_listener.py](file:///c:/MGM/mgm-network-vault/network_vault/syslog_listener.py)) listening on port 514 (configurable).
  - Automatically intercepts commit and configuration save events in real time from network switches:
    - **Juniper Junos (EX2200 / EX2300 / EX3300)**: Detects `UI_COMMIT_COMPLETED`, `UI_COMMIT_PROGRESS`, and `UI_COMMIT:`.
    - **Cisco Catalyst 1300 / CBS / SG**: Detects `COPY - TRAP` (copy running-config to startup-config) and `COPY - FILECPY`.
    - **Cisco IOS / IOS-XE**: Detects `%SYS-5-CONFIG_I: Configured from console` and `%PARSER-5-CFGLOG_LOGGEDCMD`.
  - **Debounce / Hold-down Timer**: Configurable cooldown window (default 45s) to coalesce multiple rapid commits during admin maintenance sessions before initiating backup.
  - **Read-Only Command Exclusion**: Safely ignores inspection commands (e.g. `show system commit`, DOM optics, MAC/ARP live tools) to prevent false-positive backup loops.
  - **Per-Device Syslog Storage**:
    - Saves incoming raw syslog streams to dedicated daily files partitioned by device hostname (`syslog-<hostname>-YYYY-MM-DD.log`).
    - Configured storage path targeted to local storage volume (`D:\NetworkVault\syslog\`).
  - Added Syslog Listener status and configuration display in the **Settings** view.

## [1.5.1] - 2026-10-13

### Fixed
- **Port Locator Navigation & Trace Log Persistence**:
  - Resolved issue where navigating between menus (Dashboard, Devices, Backups, etc.) while a host trace was in progress caused the "Tracing Host Port Across Switch Fabric" modal to show empty statistics and a blank terminal console upon return.
  - Implemented persistent server-side trace state in `_LOCATOR_RUNS` storing all accumulated console logs (`logs`), step-by-step progress, `resolved_ip`, and `resolved_mac`.
  - Updated `/api/operations/port-locator/status/<run_id>` to deliver real-time logs and resolution telemetry to connecting and returning clients.
  - Added immediate client-side synchronization (`syncLocatorModalData`) so returning to Port Locator or restoring the minimized dock instantly populates the terminal logs, switches queried, progress percentage, resolved IP, resolved MAC, and path matches without delay.
  - Added a dedicated **Trace Console** button in the target host banner so users can re-open the real-time terminal trace console and review full diagnostic logs at any time.

### Improved
- **Trunk & Edge Port Classification**:
  - Enhanced trunk interface parsing to support Cisco interface abbreviations (e.g., `Fa4/0/48` ↔ `FastEthernet4/0/48`, `Gi`, `Te`, `Po`) with `switchport mode trunk` and channel groups.
  - Strengthened Junos hierarchical brace parsing for `port-mode trunk`, `interface-mode trunk`, `vlan members all`, `802.3ad` LACP bundles, and transit descriptions.
  - Fixed Flask application context management inside parallel background threads.

## [1.5.0] - 2026-10-10

### Added
- **MAC / IP Port Locator across Switches**:
  - Live discovery tool located under the new **Operations** sidebar navigation (`/operations/port-locator`).
  - Search any endpoint by **IPv4 address** (e.g. `10.10.45.120`) or **MAC address** (colon, Cisco dot, dash, or unformatted hex).
  - Automatically queries ARP tables across core and distribution switches to resolve IP-to-MAC mapping, then queries MAC address tables across campus switches in parallel.
  - **Edge vs Uplink Distinction & Accurate Trunk Validation**: Validates Junos `port-mode trunk`, `interface-mode trunk`, `vlan members all`, `802.3ad` LACP bundles, `native-vlan-id`, Cisco `switchport mode trunk`, and uplink descriptions (`uplink-to-distribution`, `to-dist`, `dsw`, etc.) from switch backups, ensuring transit hops are never falsely classified as direct edge ports.
  - **Persistent Floating Search Dock**: Minimized Port Locator search widget stays consistently visible and updates live across all pages and sidebar menus (Dashboard, Devices, Backups, Optics, Settings), allowing seamless navigation during searches.
  - **OUI Vendor Hardware Identification**: Integrated catalog of enterprise and hospital hardware vendors (VMware, Philips Healthcare, GE Healthcare, Mindray, Siemens Healthineers, Apple, Cisco, Juniper, etc.) to immediately identify connected device types.
  - **Live Trace Progress Window & Terminal Console**: Real-time streaming modal with progress bar, step status, and auto-scrolling terminal logs showing step-by-step ARP resolution and switch table probing.
  - Site-aware scoping and permissions adhering to multi-site access control.

- **Optical Transceiver (SFP / SFP+) Signal Health (DOM/DDM)**:
  - Dedicated campus-wide fiber diagnostics monitor (`/operations/optics`) for checking fiber link health.
  - **Real-Time Scan Progress Window & Streaming Terminal**: Animated progress modal with live terminal logs streaming each switch connection, SFP detection, RX/TX power levels, and diagnostic results in real time.
  - Real-time optical telemetry polling over SSH:
    - Junos: `show interfaces diagnostics optics`
    - Cisco Catalyst 1300 / CBS / SG: `show optical-transceiver-diagnostics` / `show fiber-ports optical-transceiver`
    - Cisco IOS: `show interfaces transceiver detail`
  - Parses Receiver Optical Power (RX dBm), Transmitter Output Power (TX dBm), Module Temperature (°C), Supply Voltage, and Laser Bias Current.
  - **Intelligent Health Grading & Visual Gauges**:
    - **Optimal Signal (Green)**: Within standard operating margin (-1 dBm to -18 dBm).
    - **Low Signal Warning (Amber)**: High optical attenuation (-18 dBm to -28 dBm), alerting engineers to dirty patches or high fiber bend loss.
    - **Critical / Loss of Light (Red)**: RX signal <= -28 dBm or loss-of-signal alarm, identifying dark fibers or broken transceiver lasers.
  - Integrated into single switch view: "Optical transceivers (DOM/DDM)" live tool button on [device_detail.html](file:///l:/MGM/network-vault/network_vault/templates/device_detail.html) with interactive modal breakdown.

- **MAC & ARP Live Inspection Tools**:
  - Direct live tools on Device Detail page for instant inspection of switch MAC address forwarding tables and ARP tables without manual terminal access.

## [1.4.2] - 2026-10-09

### Fixed
- **Device List Sorting**:
  - Aligned the main **Devices** table list and CSV export to sort by device level/group from top to bottom (`Level-11` → `Level-1` → `Ground` → `Utility` → `Distribution`) followed by switch name, mirroring the group filter dropdown order.

## [1.4.1] - 2026-10-09

### Changed
- **Device Grouping Standardized to Levels**:
  - Migrated device group nomenclature from `Floor-X` to `Level-X` across database models, device inventory, and backup schedules.
  - Standardized custom top-to-bottom natural sort order across all group dropdowns and dashboard sections:
    `Level-11` → `Level-10` → `...` → `Level-1` → `Ground` → `Utility` → `Distribution`.
  - Applied the natural sort order to the **Devices** filter dropdown, **Device Edit/Add** group datalist, **Schedules** target group selector, and the **Dashboard** Level/Group cards.

## [1.4.0] - 2026-10-08

### Added
- **Multi-Site User Tagging**: Non-group-admin users (`site-admin`, `read-write`, `read-only`) can now be assigned to multiple sites rather than strictly a single site.
  - **Secondary Association (`user_sites`)**: Many-to-many relationship allowing flexible assignment of any combination of hospital campus sites to a user account.
  - **Dynamic Site Switcher**: Users tagged with multiple sites now receive an interactive site switcher dropdown in the header bar, enabling seamless switching between their authorized sites or viewing aggregate data across "All My Sites".
  - **Multi-Site Query Scoping**: Universal filtering helper (`_site_filter`) prevents unauthorized data exposure across all views (Dashboard, Devices, Backups, Schedules, Credentials, and Logs).
  - **User Management Interface**:
    - Distinct tag badges on the Users table showing all assigned sites for each user.
    - Inline site tag manager (`Sites (N)`) allowing Group Administrators to toggle and save multiple sites per user.
    - Multi-select pill checkboxes in the "Add new user" form for convenient multi-site assignment upon creation.
  - **Multi-Site Device Forms**: Site Administrators tagged with multiple sites can choose the designated site when creating or editing switches.

## [1.3.0] - 2026-10-08

### Added
- **Multi-Site Architecture**: Support for multiple hospital networks and campus locations:
  - Default pre-configured sites: **MGMHC** (Main Hospital), **MGMCI** (Cancer Institute), **MGM-Malar** (Malar Hospital), and **MGM-Sevenhills** (Sevenhills Hospital).
  - Switches, credential profiles, schedules, and backups are strictly partitioned per site.
- **Hierarchical Role-Based Access Control (RBAC)**:
  - **Group Administrator (`group-admin`)**: Full admin access across all sites, can switch between individual sites or view "All Sites (Group View)", manage sites, and assign users to sites.
  - **Site Administrator (`site-admin`)**: Full admin rights restricted exclusively to their designated site. Can manage their site's switches, credentials, schedules, backups, and site users.
- **Global Header Site Switcher**: Modern floating selector pill in the header allowing Group Administrators to seamlessly filter or view all sites in real-time.
- **Sites Management View (`/sites`)**: Dedicated administration panel in the sidebar for Group Administrators to add, update, and manage network sites.

## [1.2.1] - 2026-10-08

### Added
- **Backups Search Filter**: Added instant multi-field search (`q` query) in the Backups view supporting search across switch name, host IP, location, git commit hash, trigger, and backup path.
- **Persistent Backup Progress Indicator**: Running device or full-network backups now persist their status toast across sidebar navigation and page changes, continually polling the active job until completion.
- **Enhanced Transitions & Micro-Animations**: Upgraded page and card entrance/exit transitions with smoother motion curves and enhanced sidebar nav tap ripples.

### Fixed
- **Streamlined "Changes Only" Toggle**: Replaced irregular checkbox styles with an iOS/modern switch toggle featuring an integrated animated checkmark.
- **Script Cache Invalidation**: Added version query param (`?v={{ app_version }}`) to `app.js` script tag in base layout.

## [1.2.0] - 2026-10-08

### Added
- **In-App Software Updates**: Added automated update checking and installation directly within the web UI (**Settings → Software Update**).
  - Admin users can check the remote GitHub repository for new release tags and commits.
  - One-click **Install update & restart**: pulls latest code (`git pull origin main`), upgrades dependencies, and triggers an automated service restart without needing manual CLI access on the server.
  - Linked the sidebar version badge directly to the Software Update panel for instant status inspection.

## [1.1.1] - 2026-10-07

### Fixed
- **Cisco Catalyst 1300 / CBS / SG SSH Authentication**: Added dual-mode Netmiko driver (`CiscoC1300SSH`) that automatically falls back to in-band interactive terminal authentication (`User Name:`, `Password:`) when switch firmware has transport-level `ip ssh password-auth` disabled by factory default.
- **Sticky Sidebar on Page Scroll**: Fixed sidebar scrolling out of view on tall pages by removing conflicting `position: relative` and locking the sidebar to `position: sticky; top: 0; height: 100vh;` with independent navigation scrolling.
- **Asset Cache Invalidation**: Added app version query parameter (`?v={{ app_version }}`) to stylesheet links to prevent stale browser CSS caches.

## [1.1.0] - 2026-10-07

### Added
- **Floorwise Dashboard & Visual Metrics**: Replaced flat device tables on the dashboard with Reachability and Backup Status donut charts, backed by collapsible Floor/Group device cards.
- **Floor Card Controls**: Added "Expand All" / "Collapse All" toggle button for rapid overview of device states across all floors.
- **Custom Floor Sort Order**: Floor cards naturally order descending from top floors (e.g. Floor 11) down to Ground Floor, followed by Utility / custom groups.
- **Collapsible Sidebar**: Compact sidebar toggle for all menus, persisting navigation state via browser storage.
- **Navigation Back Button**: Universal back button in the header bar with history fallback.
- **Role-Based Access Control (RBAC)**: Added `admin`, `read-write`, and `read-only` user roles with permission enforcement across settings, user management, and device actions.
- Automatic database schema migration and backfill ensuring existing accounts default safely to `admin` without lockout.

### Fixed
- Chart.js Reachability donut legend formatting and text alignment when expanding the dashboard layout.
- Null-safe user role checks across authentication session decorators and model helpers.

## [1.0.1] - 2026-10-07

### Added
- `docs/WINDOWS-SERVER.md`: step-by-step Windows Server guide covering prerequisites, dependencies, the service
  (installer, Task Scheduler or NSSM), IIS reverse proxy with HTTPS, updates and troubleshooting.
- `deploy/iis/web.config` and `deploy/iis/setup-iis.ps1` to publish the app through IIS (URL Rewrite + ARR).

### Changed
- Re-running `install-windows.ps1` no longer re-opens the app port in the firewall when the app is published through IIS.

## [1.0.0] - 2026-10-07

First release.

### Backups
- On-demand backups of one, selected or all switches, in parallel with live progress.
- Juniper EX2200 / EX2300 / EX3300, including Virtual Chassis: hierarchical and `display set`
  config, version, hardware, commit history and VC member status.
- Cisco Catalyst 1300 / CBS and IOS: running config, version, inventory and stack status.
- Legacy SSH support for older Junos (EX2200/EX3300 on 12.x).
- Model, software version, serial number and VC membership detected automatically after CSV import.

### Scheduling, change tracking and monitoring
- Hourly, daily, weekly, monthly or cron schedules targeting all devices, a group, a role,
  a platform or a hand-picked list.
- Change detection that ignores volatile lines, with +/- line counts and the Junos commit that caused it.
- Side-by-side and unified diffs of any two backups; Junos rollback compare and Cisco unsaved-changes check.
- Reachability monitoring (ping + SSH port) and alerts when a VC member goes missing.

### Management
- Log viewer with filters, search, CSV export and retention.
- Downloads: single files, ZIP of selected backups, or the latest backup of every switch.
- Retention by count and age, with pinned backups kept forever.
- Users with forced password change, CSRF protection, encrypted credential storage.

### GitHub mirror
- Backups mirrored to a private GitHub repository: one dated commit per change, a tag per backup run,
  and existing backups imported as history. Secrets are masked by default.

### Deployment
- `config.toml` for server settings.
- Encrypted export/import of the whole installation (`.nvault`) from the web UI or `manage.py`.
- Windows Server installer (start-at-boot service with auto-restart) and Docker/Linux files.
- MGM Healthcare branding.
