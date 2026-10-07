# Changelog

All notable changes to MGM Network Vault. Versions follow [Semantic Versioning](https://semver.org):
**MAJOR** = breaking change (e.g. export format, removed feature), **MINOR** = new feature,
**PATCH** = bug fix. The version is in `network_vault/version.py` and shown in the app's sidebar.

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
