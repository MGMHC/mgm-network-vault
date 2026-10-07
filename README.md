# MGM Network Vault — switch configuration backup manager

Web app for backing up Juniper EX (EX2200 / EX2300 / EX3300, including Virtual Chassis) and Cisco
(Catalyst 1300 / CBS / IOS) switch configurations.

## Features

| Area | What it does |
|---|---|
| **On-demand backup** | One switch, selected switches, or all switches, run in parallel with a live progress toast. |
| **Scheduled backup** | Hourly, daily, weekly, monthly or custom cron. Targets can be all devices, a group, a role (core/access), a platform or a hand-picked list. *Run now* button on each schedule. |
| **Change detection** | Every backup is hashed (timestamps and other lines that change on their own are ignored). Changed backups are flagged with +/− line counts and the Junos commit that caused them (`show system commit`). |
| **Compare** | Side-by-side or unified diff of any two backups, including from different switches. For Junos you can diff the `display set` format, which is much easier to read. Diffs can be downloaded as `.diff` files. |
| **Live tools** | Junos: commit history and `show system rollback N compare M`. Cisco: unsaved changes (startup vs running). Both: VC/stack status and an SSH login test. |
| **Virtual Chassis** | On every backup, VC members are parsed (serial, model, role, Prsnt/NotPrsnt). If a member drops out you get a dashboard alert and a WARN log. |
| **Reachability** | Background ICMP ping + TCP/22 check every N minutes, plus manual checks. Status is **Up**, **Ping only** (SSH closed) or **Down**. Only state changes are logged. |
| **Downloads** | Each backup has a `.conf` file, a `.set` file (Junos) and a `.info.txt` file (version, chassis hardware, commit history, VC). You can also download a ZIP of selected backups or of the latest backup of every switch. |
| **Logs** | Every action is logged with user, device, level and category. You can filter, search and export to CSV, and purge or auto-expire old entries. |
| **Retention** | Keep N backups per device and/or N days. You can *Pin* a backup to keep it forever. The newest backup is never deleted. Optionally skip storing configs that haven't changed. |
| **GitHub sync** | Every backup is mirrored to a private GitHub repository: one commit per configuration change (dated with the backup time), a `backup/YYYY-MM-DD_HHMMSS` tag per run that changed something, and existing backups imported as history on first sync. Secrets are masked in the GitHub copy by default. If GitHub is unreachable, pushes are retried every 15 minutes. See [GitHub sync](#github-sync). |
| **Security** | Login is required, with a forced password change on first login and CSRF protection. Device passwords are encrypted at rest (Fernet), and you can add multiple users. |

## Quick start (this PC)

```bat
cd L:\MGM\network-vault
start.bat
```

Then open **http://<server-ip>:8080** and sign in as **admin / admin**. You'll have to change the password straight away.

1. **Credentials** → create a profile, e.g. `juniper` and `cisco`.
2. **Devices** → *Add device*, or import a CSV (use the *Download template* link). You don't enter the
   model: the app logs in right after import and detects the model, software version, serial number and
   Virtual Chassis members (*Detect model* re-checks selected switches; every backup refreshes them too).
   - Tick **Virtual Chassis** for EX2300/EX2200/EX3300 stacks.
   - Tick **Legacy SSH** if an older EX2200/EX3300 (Junos 12.x) refuses to log in.
3. **Dashboard** → *Check all reachability*, then *Backup all now*.
4. **Schedules** → for example, *Daily 02:00 – all devices*, plus *Every 4 h – role: core*.

Server settings (port, data folder, proxy) are in `config.toml`, which is created from `config.example.toml` on first start.

## Switch-side account (recommended: read-only)

**Juniper (Junos):** `view-configuration` is needed so that secrets are included as `$9$` hashes and the backup can be restored.
```
set system login class NETWORK-VAULT permissions [ view view-configuration ]
set system login user netvault class NETWORK-VAULT authentication plain-text-password
```

**Cisco C1300:** `show running-config` needs level 15.
```
username netvault privilege 15 password <password>
```

## GitHub sync

1. On GitHub, create a **private**, empty repository (e.g. `switch-configs`).
2. Create a **fine-grained personal access token**. Give it access to *only that repository* with
   **Contents: Read and write**, and set an expiry date. Put a reminder in your calendar to renew it.
3. In the app, go to **GitHub sync**, paste the HTTPS address and the token, tick *Sync backups to GitHub*, and click **Save & sync**.

The first sync replays all stored backups, oldest first, so the repository starts with their full history.
After that, each backup that changes a switch's config becomes one commit and is pushed when the backup run ends.

Repository layout: `devices/<switch>/config.conf`, `config.set` (Junos) and `info.txt`. Use the folder's *History*
on GitHub, or `git log -p devices/<switch>`, to see every version. Because GitHub keeps all history, it also works as a
long-term archive for versions that local retention has already deleted.

Masking (on by default) replaces password hashes, SNMP communities and RADIUS/TACACS keys with `<masked>` in the
GitHub copy only. Local backups stay complete and restorable. Git for Windows must be installed on the server.
The token is stored encrypted, and Windows' Git credential manager is bypassed so no login pop-ups block scheduled runs.

## Hosting on a server

### Windows Server
1. Install **Python 3.12+** (tick *Install for all users* and *Add to PATH*) and **Git for Windows**.
2. Copy the app to the server: use *Export & migrate → Download app package*, or `python manage.py package app.zip`, and unzip it, e.g. to `C:\NetworkVault`.
3. In PowerShell **as Administrator**:
   ```powershell
   cd C:\NetworkVault\network-vault
   powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Port 8080 -DataDir "D:/NetworkVault/data"
   ```
   This installs the dependencies, writes `config.toml`, opens the firewall port, and registers the
   **MGM Network Vault** scheduled task. The task starts at boot as SYSTEM, so no one needs to be logged on,
   and restarts the app if it stops. Logs go to `logs\service.log`. Re-run the script to update; `deploy\uninstall-windows.ps1` removes the service and keeps the data.

### Linux (Docker)
```bash
cd network-vault
docker compose -f deploy/docker-compose.yml up -d --build
```
Data is kept in the `network-vault-data` volume. Set `TZ` and, if needed, `HTTPS_PROXY` in `deploy/docker-compose.yml`.

### Moving your data from this PC
1. Old PC: **Export & migrate → Download export**. You'll get one encrypted `.nvault` file protected by a passphrase you choose.
2. New server: sign in as `admin`/`admin`, go to **Export & migrate**, upload the file with the passphrase, then **Restart now**.
   The current data on the new server is kept in `data\pre-restore-<time>\`.
3. Check the dashboard, then stop the app on the old PC so switches aren't backed up twice. If GitHub sync is on,
   click **Sync now** on the new server; it reconnects to the existing repository and history.

Command line: `python manage.py export file.nvault`, then on the new server, with the app stopped: `python manage.py import file.nvault`.
For automated off-site copies, set `NETWORK_VAULT_PASSPHRASE` and schedule `manage.py export`.

### HTTPS
The app itself serves plain HTTP. For access across the organisation, publish it through IIS (URL Rewrite/ARR) or nginx with
your certificate, then set `behind_proxy = true` and `secure_cookies = true` in `config.toml`.

### config.toml
| Key | Default | Meaning |
|---|---|---|
| `host` | `0.0.0.0` | Listen address (all network cards) |
| `port` | `8080` | Web port |
| `data_dir` | `data` | Database, keys and backups. Can be an absolute path, e.g. `D:/NetworkVault/data` |
| `threads` | `12` | Web server threads |
| `behind_proxy` | `false` | Trust `X-Forwarded-*` headers from IIS/nginx |
| `secure_cookies` | `false` | Only send the login cookie over HTTPS |

Each key can be overridden with an environment variable `NETWORK_VAULT_<KEY>`, e.g. `NETWORK_VAULT_PORT=9000`.

Other tools: `python manage.py info` shows the paths and counts; `python manage.py reset-password admin` resets a forgotten password.

## Data & what to back up

```
data\
  network_vault.db  SQLite: devices, backups index, schedules, logs, users
  secret.key        Encryption key for device passwords  ← keep this safe; without it stored passwords can't be decrypted
  flask.key         Session signing key
  backups\<device>\<device>_YYYYMMDD-HHMMSS.conf | .set | .info.txt
```
Back up the whole `data` folder, or use **Export & migrate** for a single encrypted file. The config files are plain text, so they can also be read without the app.

## Versions & releases

The current version is in `network_vault/version.py`. It's shown in the sidebar and on the login page,
printed at start-up, and returned by `python manage.py --version`. Every release is a Git tag
(`v1.0.0`, `v1.1.0`, ...) with its notes in [CHANGELOG.md](CHANGELOG.md).

To make a new release:
1. Bump `__version__` in `network_vault/version.py`: patch for fixes, minor for new features, major for breaking changes.
2. Add a section at the top of `CHANGELOG.md`.
3. Commit, tag and push:
   ```bash
   git commit -am "Release v1.1.0"
   git tag -a v1.1.0 -m "MGM Network Vault 1.1.0"
   git push --follow-tags
   ```
4. On GitHub, go to **Releases → Draft a new release**, choose the tag, and paste that version's CHANGELOG section.

To update a server to a release: `git fetch --tags && git checkout v1.1.0`, then re-run `deploy\install-windows.ps1`.
On Docker, re-run `docker compose ... up -d --build`. Take an export first.

## Notes

- `paramiko` is pinned below 4.0 on purpose. Paramiko 4 removed `ssh-rsa` and SHA-1 key exchange, and older Junos on EX2200/EX3300 still needs them.
- Cisco C1300 uses Netmiko's `cisco_s300` driver. Classic Catalyst IOS switches use `cisco_ios`.
- Core switches are just devices with role **core**. Use them as a schedule target to back them up more often.
