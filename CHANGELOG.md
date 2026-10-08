# Changelog

All notable changes to MGM Network Vault. Versions follow [Semantic Versioning](https://semver.org):
**MAJOR** = breaking change (e.g. export format, removed feature), **MINOR** = new feature,
**PATCH** = bug fix. The version is in `network_vault/version.py` and shown in the app's sidebar.

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
