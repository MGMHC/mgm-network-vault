# MGM Network Vault — switch & firewall configuration backup manager

Web app for backing up Juniper EX (EX2200 / EX2300 / EX3300, including Virtual Chassis), Cisco
(Catalyst 1300 / CBS / IOS), and SonicWall (SonicOS / NSa) network configurations with multi-site campus isolation, real-time LLDP port tracing, automated email alerts, hardware lifecycle tracking, and automated GitHub sync.

## Core Features

| Area | What it does |
|---|---|
| **Multi-site & Campus Isolation** | Dedicated site contexts (`MGMHC`, `MGM-Malar`, `MGM-CORP`, etc.) with campus filtering, transferrable credential profiles, role-scoped access control, and isolated per-site Git backup branches. |
| **On-demand & Scheduled Backups** | Parallel backups on demand or on automated schedules (hourly, daily, weekly, custom cron) targeting all switches, specific groups, roles, platforms, or custom selections. |
| **Change Detection & Visual Diff** | Content-hashed backups (variable timestamps and volatile lines ignored). Side-by-side and unified diffs with line-level addition/removal metrics, Junos `display set` diffing, and `.diff` file downloads. |
| **LLDP-Traced Port Locator** | Searches MAC or IP addresses across the campus. Traces from distribution/core switches down through LLDP neighbour hops (including LAG / `ae` bundles and Cisco port-channels) directly to the edge access port in 2–3 switch queries instead of scanning the whole fleet. Supports Group/Location scope filtering and full-path tracing. |
| **Centralized Email Alerts** | Dedicated **Alerts** control center (`Admin → Alerts`) with customized HTML templates, live preview, test emails, and per-message recipient routing across three core triggers: **Switch Down / Recovered**, **Backup Failed**, and **Config Changed** (with masked diff snippets). |
| **IPAM & Subnets** | IPv4 subnet and IP address management with automatic switch ARP table discovery, VRF scoping, VLAN tagging, gateway assignment, utilization meters, and duplicate IP detection. |
| **VLAN & Port Audit** | Live VLAN matrix across all switches and dormant port auditing (identifies unused ports down for 30, 60, or 90+ days to reclaim switch capacity). |
| **Rack Elevations** | Visual 42U rack elevation diagrams organized by room and rack, mapping installed switches, patch panels, and server units with live status indicators. |
| **Hardware Health & Optical Diagnostics** | Real-time hardware health dashboard (chassis temperature, fan status, power supplies, CPU/memory utilization) and SFP optical transceiver diagnostics (TX/RX optical power levels against alarm/warning thresholds). |
| **Hardware & Firmware Lifecycle** | Milestone tracking (End of Sale, End of Support, End of Life), OS build version distribution, Gold Standard firmware baseline audit, and fleet health scores across Juniper, Cisco, and SonicWall. |
| **SonicWall Next-Gen Firewalls** | Native SonicOS driver support (NSa 4800, etc.) with automated configuration collection, hardware/firmware fact discovery, and syslog commit event triggers. |
| **Reachability & Live Diagnostics** | Background ICMP ping + TCP/22 check every 5 minutes. Live switch tools: Junos rollback comparisons, commit history, Cisco unsaved running-vs-startup diffs, VC stack member status, and SSH login checks. |
| **Automated GitHub Sync** | Per-site isolated branches on GitHub (`MGMHC`, `MGM-Malar`, etc.) with thread-safe working directories. Every configuration change creates a dated commit and tag. Sensitive secrets (passwords, SNMP communities, RADIUS/TACACS keys) are automatically masked in the GitHub copy. |
| **Security & Role-Based Access** | Group Administrator, Site Administrator, Operator, and Read-Only roles. Fail-closed authorization guards, constant-time CSRF verification, password version session tracking, and AES-Fernet credential encryption at rest. |

## Quick Start (Local / Dev)

```bat
cd C:\MGM\mgm-network-vault
start.bat
```

Open **http://localhost:8080** and sign in with the initial administrator credentials. You will be prompted to change the password immediately upon first login.

1. **Credentials** (`Admin → Credentials`) → Add shared credential profiles (e.g. `juniper`, `cisco`).
2. **Devices** → Click *Add switch*, or import from CSV using the downloadable template. The app logs in automatically upon addition to detect the model, firmware, serial number, and Virtual Chassis members.
   - Select **Virtual Chassis** for EX2200 / EX2300 / EX3300 stacks.
   - Select **Legacy SSH** if legacy EX2200 / EX3300 switches running Junos 12.x require legacy cipher negotiation.
3. **Dashboard** → Click *Check all reachability*, then *Backup all now*.
4. **Schedules** → Create automated backup schedules (e.g., Nightly at 02:00 for all switches, every 4 hours for core switches).

## Switch-Side Service Account Setup

Use a read-only account with configuration-view permissions wherever possible:

### Juniper (Junos)
`view-configuration` permission is required so configuration secrets are included as `$9$` hashes and can be restored:
```junos
set system login class NETWORK-VAULT permissions [ view view-configuration ]
set system login user netvault class NETWORK-VAULT authentication plain-text-password
```

### Cisco (Catalyst / CBS / C1300)
`show running-config` requires privilege level 15:
```ios
username netvault privilege 15 secret <password>
```

### SonicWall (SonicOS / NSa)
A read-only non-config administrator user or standard administrator account.

## Operations & Diagnostics

Under the **Operations** navigation menu:
- **Port Locator** (`/operations/port-locator`): Trace any MAC or IP address directly to its physical switch and port via automated LLDP topology traversal.
- **Hardware Health** (`/operations/health`): Fleet-wide environmentals, fans, power supplies, CPU/memory usage, and chassis temperature sensors.
- **Optical Health** (`/operations/optics`): Real-time optical transceiver power (dBm), transmitter bias, and link health diagnostics.
- **IPAM & Subnets** (`/ipam`): Campus subnet planner and IP tracker with ARP discovery.
- **Rack Elevations** (`/operations/racks`): Visual rack cabinet layouts.
- **VLAN & Port Audit** (`/operations/vlan-audit`): Campus-wide VLAN deployment matrix and dormant port detector.
- **Lifecycle & EOL** (`/operations/lifecycle`): End-of-life vulnerability analysis and firmware baseline tracker.

## GitHub Sync Architecture

Every site commits and pushes configuration backups to its own dedicated branch on GitHub:
* `MGMHC` &rarr; branch `MGMHC`
* `MGM-Malar` &rarr; branch `MGM-Malar`
* `MGM-CORP` &rarr; branch `MGM-CORP`

1. In GitHub, create a **private** repository (e.g. `mgm-network-vault-backups`).
2. Generate a fine-grained Personal Access Token (PAT) with `Contents: Read and write` permissions restricted only to that repository.
3. In Network Vault, navigate to **Admin &rarr; GitHub sync**, enter the repository HTTPS URL and token, enable sync, and click **Save & sync**.

Each site branch stores backups under `devices/<device>/config.conf`, `config.set` (Junos), and `info.txt`. Masking (enabled by default) redacts passwords, SNMP strings, and keys in the GitHub repository while keeping local copies complete and restorable.

## Windows Server Deployment

> **Full guide:** [docs/WINDOWS-SERVER.md](docs/WINDOWS-SERVER.md)

1. Install **Python 3.12+** (check *Add Python to PATH*) and **Git for Windows**.
2. Deploy the application package to `C:\NetworkVault` (via *Admin &rarr; Export & migrate &rarr; Download app package* or `python manage.py package app.zip`).
3. Run PowerShell as Administrator:
   ```powershell
   cd C:\NetworkVault\network-vault
   powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Port 8080 -DataDir "D:/NetworkVault/data"
   ```
4. Configure service parameters in `config.toml` (copy from `config.example.toml`).
5. Use [`restart_vault.bat`](restart_vault.bat) (Run as Administrator) to safely cycle the Windows Service and IIS application pool after configuration changes or updates.

### Configuration (`config.toml`)

```toml
[server]
host = "0.0.0.0"                               # Listen address
port = 8080                                    # Web application port
data_dir = "D:/NetworkVault/data"               # SQLite database, encryption keys, logs
backup_dir = "D:/NetworkVault/Config-Backups"   # Stored configuration backups directory
threads = 12                                   # Waitress worker threads
behind_proxy = true                             # Trust X-Forwarded-* headers (IIS / reverse proxy)
secure_cookies = false                          # Set to true when terminating HTTPS on IIS

[syslog]
enabled = true                                  # Real-time syslog listener for commit backups
port = 514                                     # UDP syslog port
debounce_sec = 45                               # Wait window after commit before triggering backup
log_dir = "D:/NetworkVault/syslog"              # Syslog stream storage on disk
```

Settings can also be overridden with environment variables (e.g., `NETWORK_VAULT_PORT=8080`).

## Backups & Disaster Recovery

The application stores all critical persistent state inside `data/` and `backup_dir`:
```text
data/
  network_vault.db    SQLite: inventory, schedules, alert rules, logs, users, IPAM
  secret.key          AES-Fernet master key for encrypted credentials (MUST be backed up)
  flask.key           Session cookie signing key
D:/NetworkVault/Config-Backups/
  <device>/<device>_YYYYMMDD-HHMMSS.conf | .set | .info.txt
```

### Export & Migration
Navigate to **Admin &rarr; Export & migrate**:
- **Export**: Creates a single passphrase-encrypted `.nvault` bundle containing the database, keys, and backups.
- **Import**: Restores an `.nvault` archive onto a new server with automatic pre-restore backup safeguards.
- CLI equivalents: `python manage.py export backup.nvault` and `python manage.py import backup.nvault`.

## Versions & Releases

The active version is defined in [`network_vault/version.py`](network_vault/version.py) and tracked with Git tags:
- **Patch (1.17.x)**: Bug fixes and minor visual improvements.
- **Minor (1.x.0)**: New features, diagnostic tools, or architectural additions.
- **Major (x.0.0)**: Breaking changes or major migrations.

See [`CHANGELOG.md`](CHANGELOG.md) for full historical release notes across all versions.
