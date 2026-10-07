# MGM Network Vault on Windows Server

Step-by-step guide for Windows Server 2016 / 2019 / 2022 / 2025. It covers:

1. [Before you start](#1-before-you-start)
2. [Install the prerequisites](#2-install-the-prerequisites)
3. [Get the application](#3-get-the-application)
4. [Install dependencies and run as a service](#4-install-dependencies-and-run-as-a-service)
5. [First login and moving your data](#5-first-login-and-moving-your-data)
6. [Publish through IIS with HTTPS](#6-publish-through-iis-with-https)
7. [Day-to-day operation](#7-day-to-day-operation)
8. [Updating to a new version](#8-updating-to-a-new-version)
9. [Troubleshooting](#9-troubleshooting)

Every command below is run in **PowerShell as Administrator** unless stated otherwise.

---

## 1. Before you start

| Need | Why |
|---|---|
| A Windows Server VM with 2 vCPU, 4 GB RAM and 20 GB free disk | Configs are small. Allow about 1 GB per 100 switches per year of history. |
| Network access from the server to the **switch management IPs on TCP 22** (and ICMP for ping) | Backups use SSH. Ask the firewall team to allow the server to reach the management VLANs. |
| Outbound **HTTPS (443)** to `github.com` and `pypi.org` | Used for GitHub sync and to install dependencies. If you're behind a proxy, see [Proxy](#proxy). |
| A DNS name, e.g. `netvault.mgmhealthcare.in`, and a TLS certificate for it | Needed for IIS/HTTPS (section 6). An internal CA certificate is fine. |
| Local administrator rights on the server | |

## 2. Install the prerequisites

### Python 3.12 or newer
1. Download the **Windows installer (64-bit)** from https://www.python.org/downloads/windows/.
2. Run it and choose **Customize installation**:
   - tick **Add python.exe to PATH**
   - on *Advanced Options*, tick **Install Python for all users**. This is required because the service runs as SYSTEM.
     The install location should be `C:\Program Files\Python3xx`.
3. Open a **new** PowerShell window and check:
   ```powershell
   python --version
   ```

### Git for Windows
Needed for GitHub sync and to download and update the app.
1. Download it from https://git-scm.com/download/win and install with the defaults.
2. Check it in a new PowerShell window:
   ```powershell
   git --version
   ```

## 3. Get the application

Pick **one** of these options.

**A. From GitHub (recommended, makes updates easy)**
```powershell
mkdir C:\Apps -Force; cd C:\Apps
git clone https://github.com/MGMHC/mgm-network-vault.git network-vault
cd network-vault
git checkout v1.1.1        # or the latest release tag
```
The repository is private, so Git asks you to sign in to GitHub the first time. Use an account that has access to `MGMHC/mgm-network-vault`.

**B. From the app package**
On the old installation, open **Export & migrate → Download app package**. Copy the zip to the server and extract it to
`C:\Apps\`, which gives you `C:\Apps\network-vault\`.

> Keep the app on a local disk. Put the **data** folder on a disk that is backed up, e.g. `D:\NetworkVault\data`.

## 4. Install dependencies and run as a service

### Option 1: automatic installer (recommended)

```powershell
cd C:\Apps\network-vault
powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1 -Port 8080 -DataDir "D:/NetworkVault/data"
```

The installer:
- checks Python and Git
- creates `.venv` and installs the dependencies from `requirements.txt` with pip
- writes `config.toml` with your port and data folder (an existing file is kept)
- opens **TCP 8080** in Windows Firewall for Domain/Private networks
- registers the **MGM Network Vault** service task, which:
  - starts at boot as **SYSTEM**, so no one needs to be logged on
  - restarts automatically (every minute, up to 999 times) if the app stops
  - logs to `C:\Apps\network-vault\logs\service.log`
- starts the app and prints the addresses to open

Check it:
```powershell
Get-ScheduledTask "MGM Network Vault" | Select-Object TaskName, State
Invoke-WebRequest http://localhost:8080/login -UseBasicParsing | Select-Object StatusCode
```

You can run the installer again at any time. It updates the dependencies and keeps your config and data.
To remove the service (data is kept):
```powershell
powershell -ExecutionPolicy Bypass -File deploy\uninstall-windows.ps1
```

### Option 2: manual install

Use this if your policy does not allow running scripts.

1. **Dependencies**
   ```powershell
   cd C:\Apps\network-vault
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install --upgrade pip
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. **Configuration**: copy `config.example.toml` to `config.toml` and edit it:
   ```toml
   [server]
   host = "0.0.0.0"
   port = 8080
   data_dir = "D:/NetworkVault/data"
   ```
3. **Test run**: run `start.bat`. Open http://localhost:8080, then press **Ctrl+C** to stop.
4. **Service**: choose one of the following.
   - **Task Scheduler** (built in). Open *Task Scheduler → Create Task*:
     - *General* tab: Name `MGM Network Vault`, *Run whether user is logged on or not*, user `SYSTEM`, *Run with highest privileges*
     - *Triggers* tab: **At startup**
     - *Actions* tab: Program `cmd.exe`, arguments `/c "C:\Apps\network-vault\start.bat" >> "C:\Apps\network-vault\logs\service.log" 2>&1`, Start in `C:\Apps\network-vault`
     - *Settings* tab: *If the task fails, restart every* **1 minute**, up to **999** times; clear *Stop the task if it runs longer than*; *If the task is already running*: **Do not start a new instance**
   - **NSSM**, which gives a real entry in `services.msc`. Download it from https://nssm.cc, then run:
     ```powershell
     nssm install MGMNetworkVault "C:\Apps\network-vault\.venv\Scripts\python.exe" "C:\Apps\network-vault\run.py"
     nssm set MGMNetworkVault AppDirectory "C:\Apps\network-vault"
     nssm set MGMNetworkVault AppStdout "C:\Apps\network-vault\logs\service.log"
     nssm set MGMNetworkVault AppStderr "C:\Apps\network-vault\logs\service.log"
     nssm set MGMNetworkVault Start SERVICE_AUTO_START
     nssm start MGMNetworkVault
     ```
     NSSM restarts the app whenever it exits, including after **Restart now** on the Export & migrate page.
     > Use **either** the scheduled task **or** NSSM, never both. Two copies would run every backup twice; the app refuses to start a second copy on the same port.
5. **Firewall** (only if users connect directly, without IIS):
   ```powershell
   New-NetFirewallRule -DisplayName "MGM Network Vault (TCP 8080)" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Domain,Private
   ```

### Running as a dedicated service account (optional)
SYSTEM works out of the box. If your policy requires a named account (e.g. `MGM\svc-netvault`):
- give it **Modify** rights on the app folder and the data folder
- give it the **Log on as a batch job** right (Task Scheduler) or **Log on as a service** right (NSSM)
- in the task or service, change *Run as* to that account

## 5. First login and moving your data

1. Open `http://<server-name>:8080`, or `https://<host-name>` once IIS is set up.
2. Sign in as **admin / admin**. You'll be asked to set a new password straight away.
3. **Moving from the old PC:**
   - On the old PC, go to **Export & migrate → Download export** and choose a passphrase.
   - On the server, go to **Export & migrate**, upload the `.nvault` file with the passphrase, then click **Restart now**.
   - Check the dashboard: devices, backups and schedules should all be there.
   - **Stop the app on the old PC** so the switches aren't backed up twice.
   - If GitHub sync is used, open **GitHub sync** and click **Sync now**.
4. **Fresh install instead:** go to **Credentials**, then **Devices** (or a CSV import), then **Schedules**.

## 6. Publish through IIS with HTTPS

The app serves plain HTTP on port 8080. For organisation-wide use, put IIS in front of it. IIS handles HTTPS and the certificate, and forwards requests to the app, which then listens only on `127.0.0.1`.

```
Browser ──HTTPS 443──▶ IIS (URL Rewrite + ARR) ──HTTP──▶ 127.0.0.1:8080 (MGM Network Vault)
```

### 6.1 Install IIS and the two modules
1. Install IIS. Skip this if it's already installed.
   ```powershell
   Install-WindowsFeature Web-Server, Web-Mgmt-Console, Web-Http-Redirect, Web-Request-Monitor
   ```
2. Install **URL Rewrite 2.1** from https://www.iis.net/downloads/microsoft/url-rewrite (x64 installer).
3. Install **Application Request Routing 3.0** from https://www.iis.net/downloads/microsoft/application-request-routing.
4. Close and reopen *IIS Manager*.

### 6.2 Certificate and DNS
- Ask for a DNS **A record**, e.g. `netvault.mgmhealthcare.in` pointing to the server's IP.
- Import the certificate *with its private key* into **Local Computer → Personal**. For example, from a `.pfx` file:
  ```powershell
  $pw = Read-Host -AsSecureString "PFX password"
  Import-PfxCertificate -FilePath C:\certs\netvault.pfx -CertStoreLocation Cert:\LocalMachine\My -Password $pw
  ```
- Note the certificate's **thumbprint**:
  ```powershell
  Get-ChildItem Cert:\LocalMachine\My | Select-Object Subject, Thumbprint, NotAfter
  ```

### 6.3 Option A: automatic setup (recommended)
```powershell
cd C:\Apps\network-vault
powershell -ExecutionPolicy Bypass -File deploy\iis\setup-iis.ps1 -HostName netvault.mgmhealthcare.in -CertThumbprint <THUMBPRINT>
```
The script:
- enables the ARR proxy (keeps the host header, 15-minute timeout for large exports, no response buffering)
- allows the `X-Forwarded-Proto` and `X-Forwarded-Host` server variables
- creates the IIS site **MGM Network Vault** with bindings on 443 (HTTPS, SNI) and 80 (redirects to HTTPS)
- copies `deploy\iis\web.config` into `C:\inetpub\network-vault`
- changes `config.toml` to `host = "127.0.0.1"`, `behind_proxy = true` and `secure_cookies = true`
- replaces the port-8080 firewall rule with rules for 80/443, then restarts the app

### 6.3 Option B: manual setup in IIS Manager
1. **Enable the proxy:** select the server node → **Application Request Routing Cache** → *Server Proxy Settings* (right pane):
   - tick **Enable proxy**
   - set **Time-out** to `900` seconds
   - set **Response buffer threshold** to `0`
   - untick **Reverse rewrite host in response headers**
   - leave *Preserve client IP in X-Forwarded-For* ticked, and untick *Include TCP port from client IP*
   - click **Apply**
2. **Preserve the host header:** run this once.
   ```powershell
   & "$env:windir\System32\inetsrv\appcmd.exe" set config -section:system.webServer/proxy /preserveHostHeader:"True" /commit:apphost
   ```
3. **Allow the server variables:** select the server node → **URL Rewrite** → *View Server Variables* → **Add** `HTTP_X_FORWARDED_PROTO`, then **Add** `HTTP_X_FORWARDED_HOST`.
4. **Create the site:**
   - make a folder `C:\inetpub\network-vault` and copy `deploy\iis\web.config` into it
   - **Sites → Add Website**: name `MGM Network Vault`, physical path `C:\inetpub\network-vault`, binding **https**, port 443, host name `netvault.mgmhealthcare.in`, tick **Require Server Name Indication**, and pick your certificate
   - add a second binding: **http**, port 80, same host name. The rewrite rules redirect it to HTTPS.
5. **App settings:** edit `C:\Apps\network-vault\config.toml`.
   ```toml
   host = "127.0.0.1"        # only IIS can reach the app
   behind_proxy = true       # trust X-Forwarded-* from IIS
   secure_cookies = true     # login cookie only over HTTPS
   ```
   Then restart the app:
   ```powershell
   Stop-ScheduledTask "MGM Network Vault"; Start-ScheduledTask "MGM Network Vault"
   ```
6. **Firewall:** allow 80/443, and remove the 8080 rule.
   ```powershell
   New-NetFirewallRule -DisplayName "MGM Network Vault IIS (TCP 443)" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow -Profile Domain,Private
   New-NetFirewallRule -DisplayName "MGM Network Vault IIS (TCP 80)"  -Direction Inbound -Protocol TCP -LocalPort 80  -Action Allow -Profile Domain,Private
   Get-NetFirewallRule -DisplayName "MGM Network Vault (TCP 8080)" | Remove-NetFirewallRule
   ```

### 6.4 What `deploy\iis\web.config` does
- redirects `http://` to `https://`
- rewrites every request to `http://127.0.0.1:8080/...`, and sets `X-Forwarded-Proto: https` and `X-Forwarded-Host`
- raises the upload limit to 4 GB so `.nvault` exports can be imported
- adds security headers (HSTS, nosniff, SAMEORIGIN)

Publish the app at the **root** of its own host name, e.g. `https://netvault.mgmhealthcare.in/`. A sub-path such as `/netvault` isn't supported.

### 6.5 Check it
- Open `https://netvault.mgmhealthcare.in`. The padlock should show and the login page should load.
- Sign in and click around. If a page bounces you back to the login page, check that `secure_cookies = true` is only set when you're using HTTPS.
- Logs → Category *auth* shows the real client IP of each login, which confirms `X-Forwarded-For` is working.

## 7. Day-to-day operation

| Task | How |
|---|---|
| Start / stop / restart | `Start-ScheduledTask "MGM Network Vault"` and `Stop-ScheduledTask ...`, or Task Scheduler. With NSSM, use `services.msc`. |
| Service log | `C:\Apps\network-vault\logs\service.log`. Activity log: **Logs** page in the app. |
| Server info | `.venv\Scripts\python.exe manage.py info` |
| Forgotten admin password | `.venv\Scripts\python.exe manage.py reset-password admin` |
| Backup of the app itself | Include the **data folder** in your server backup, **or** schedule an encrypted export (below) |
| Change port / data folder | Edit `config.toml`, then restart. Moving the data folder means moving its contents too. |

**Scheduled off-site export.** Create a weekly task that runs:
```powershell
$env:NETWORK_VAULT_PASSPHRASE = "<long passphrase>"   # better: set it as a machine environment variable only SYSTEM/admins can read
C:\Apps\network-vault\.venv\Scripts\python.exe C:\Apps\network-vault\manage.py export "\\fileserver\backup\netvault\netvault_$(Get-Date -f yyyyMMdd).nvault"
```

## 8. Updating to a new version

1. Take an export: go to **Export & migrate → Download export**.
2. Get the new version:
   ```powershell
   cd C:\Apps\network-vault
   git fetch --tags
   git checkout v1.1.0          # the new release tag
   ```
   If you installed from the app package instead, extract the new package over the folder, keeping `config.toml`, `data` and `logs`.
3. Re-run the installer. It updates dependencies, keeps config and data, and restarts the service.
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\install-windows.ps1
   ```
   The database is upgraded automatically on start.
4. The new version number appears at the bottom of the sidebar. Release notes are in `CHANGELOG.md`.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `python` not recognised | Reinstall Python with *Add to PATH* and *for all users*, then open a new PowerShell window. |
| Service task shows *Running* but the page doesn't load | Read `logs\service.log`. Common causes: the port is already in use (another copy, or another program), or a typo in `config.toml`. |
| `Port 8080 is already in use` in the log | Another copy is running, e.g. a manual `start.bat` plus the service. Stop one of them. |
| `pip install` fails | No internet or a proxy. See [Proxy](#proxy). |
| Switches show **Down** / **Ping only** | The server can't reach the management IPs or SSH (TCP 22). Test with `Test-NetConnection 10.x.x.x -Port 22`. |
| Backups fail with authentication errors | Check the credential profile. Older EX2200/EX3300: tick **Legacy SSH** on the device. |
| IIS shows **502.3 Bad Gateway** | The app isn't running, or is listening on a different port than `web.config` (127.0.0.1:8080). |
| IIS shows **500.50 URL Rewrite error** | The `HTTP_X_FORWARDED_*` server variables aren't allowed. See 6.3 B step 3. |
| IIS shows **404.13** on import | The upload limit wasn't raised. Make sure `web.config` is in the site folder. |
| Redirects go to `http://127.0.0.1:8080/...` | `behind_proxy` isn't `true`, or *Preserve host header* / `X-Forwarded-Host` is missing. |
| Logged out right after signing in | `secure_cookies = true` but you're browsing over plain HTTP. Use the HTTPS address. |
| GitHub sync fails | Check outbound 443 to github.com, and the token's expiry date (GitHub sync page → Test connection). |

### Proxy
If the server reaches the internet through a proxy, set it machine-wide **before** installing, then restart the service:
```powershell
[Environment]::SetEnvironmentVariable("HTTPS_PROXY", "http://proxy.mgmhealthcare.in:8080", "Machine")
[Environment]::SetEnvironmentVariable("HTTP_PROXY",  "http://proxy.mgmhealthcare.in:8080", "Machine")
[Environment]::SetEnvironmentVariable("NO_PROXY",    "localhost,127.0.0.1,.mgmhealthcare.in", "Machine")
```
pip and Git (GitHub sync) both use these variables. Open a new PowerShell window afterwards.
