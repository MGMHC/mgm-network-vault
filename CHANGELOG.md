# Changelog

All notable changes to MGM Network Vault. Versions follow [Semantic Versioning](https://semver.org):
**MAJOR** = breaking change (e.g. export format, removed feature), **MINOR** = new feature,
**PATCH** = bug fix. The version is in `network_vault/version.py` and shown in the app's sidebar.

## [Unreleased]

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
