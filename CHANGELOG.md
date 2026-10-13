# Changelog

All notable changes to MGM Network Vault. Versions follow [Semantic Versioning](https://semver.org):
**MAJOR** = breaking change (e.g. export format, removed feature), **MINOR** = new feature,
**PATCH** = bug fix. The version is in `network_vault/version.py` and shown in the app's sidebar.

## [1.7.0] - 2026-10-13

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
