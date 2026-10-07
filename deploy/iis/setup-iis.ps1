<#
  MGM Network Vault - publish the app through IIS with HTTPS.

  Prerequisites (see docs\WINDOWS-SERVER.md):
    * the app is installed with deploy\install-windows.ps1 and running on port 8080
    * IIS modules installed: URL Rewrite 2.1 and Application Request Routing 3.0
    * a certificate for the host name in the server's certificate store (LocalMachine\My)

  Run in an elevated PowerShell from the app folder:
      powershell -ExecutionPolicy Bypass -File deploy\iis\setup-iis.ps1 -HostName netvault.mgmhealthcare.in -CertThumbprint <thumbprint>

  What it does:
    * installs the IIS role (Web-Server + management tools) if missing
    * enables the ARR proxy (preserve host header, 15-minute timeout, no response buffering for downloads)
    * allows the X-Forwarded-Proto / X-Forwarded-Host server variables used by web.config
    * creates the IIS site "MGM Network Vault" with HTTPS (443, SNI) and HTTP (80, redirects to HTTPS)
    * copies deploy\iis\web.config into the site folder
    * switches config.toml to listen on 127.0.0.1 only, behind_proxy = true, secure_cookies = true
    * removes the direct-access firewall rule for the app port and restarts the app service
  Re-running it is safe.
#>
param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [Parameter(Mandatory = $true)][string]$CertThumbprint,
    [string]$SiteName = "MGM Network Vault",
    [string]$SitePath = "C:\inetpub\network-vault",
    [int]$AppPort = 8080,
    [string]$TaskName = "MGM Network Vault"
)
$ErrorActionPreference = "Stop"
$App = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$appcmd = "$env:windir\System32\inetsrv\appcmd.exe"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in PowerShell started with 'Run as Administrator'."
}

# --- IIS role ----------------------------------------------------------------
if (Get-Command Install-WindowsFeature -ErrorAction SilentlyContinue) {
    $f = Get-WindowsFeature Web-Server
    if (-not $f.Installed) {
        Write-Host "Installing IIS..."
        Install-WindowsFeature Web-Server, Web-Mgmt-Console, Web-Http-Redirect, Web-Request-Monitor | Out-Null
    }
}
if (-not (Test-Path $appcmd)) { throw "IIS is not installed (appcmd.exe not found)." }
Import-Module WebAdministration

# --- required modules --------------------------------------------------------
$modules = & $appcmd list module
if (-not ($modules -match "RewriteModule")) {
    throw "IIS URL Rewrite 2.1 is not installed. Download: https://www.iis.net/downloads/microsoft/url-rewrite"
}
if (-not ($modules -match "ApplicationRequestRouting")) {
    throw "IIS Application Request Routing 3.0 is not installed. Download: https://www.iis.net/downloads/microsoft/application-request-routing"
}

# --- certificate -------------------------------------------------------------
$CertThumbprint = ($CertThumbprint -replace '[^0-9A-Fa-f]', '').ToUpper()
$cert = Get-Item "Cert:\LocalMachine\My\$CertThumbprint" -ErrorAction SilentlyContinue
if (-not $cert) { throw "Certificate $CertThumbprint not found in Cert:\LocalMachine\My. Import it first (with private key)." }
if ($cert.NotAfter -lt (Get-Date).AddDays(30)) { Write-Warning "Certificate expires on $($cert.NotAfter) - renew it soon." }

# --- ARR proxy settings (server level) ---------------------------------------
Write-Host "Configuring Application Request Routing..."
& $appcmd set config -section:system.webServer/proxy /enabled:"True" /preserveHostHeader:"True" `
    /timeout:"00:15:00" /responseBufferLimit:"0" /reverseRewriteHostInResponseHeaders:"False" `
    /includePortInXForwardedFor:"False" /commit:apphost | Out-Null
foreach ($var in "HTTP_X_FORWARDED_PROTO", "HTTP_X_FORWARDED_HOST") {
    $existing = & $appcmd list config -section:system.webServer/rewrite/allowedServerVariables
    if (-not ($existing -match $var)) {
        & $appcmd set config -section:system.webServer/rewrite/allowedServerVariables /+"[name='$var']" /commit:apphost | Out-Null
    }
}

# --- site ------------------------------------------------------------------
New-Item -ItemType Directory -Force $SitePath | Out-Null
Copy-Item "$PSScriptRoot\web.config" "$SitePath\web.config" -Force
if (-not (Get-Website -Name $SiteName -ErrorAction SilentlyContinue)) {
    Write-Host "Creating IIS site '$SiteName'..."
    New-Website -Name $SiteName -PhysicalPath $SitePath -HostHeader $HostName -Port 80 | Out-Null
} else {
    Set-ItemProperty "IIS:\Sites\$SiteName" -Name physicalPath -Value $SitePath
}
if (-not (Get-WebBinding -Name $SiteName -Protocol https -ErrorAction SilentlyContinue)) {
    New-WebBinding -Name $SiteName -Protocol https -Port 443 -HostHeader $HostName -SslFlags 1
}
$binding = Get-WebBinding -Name $SiteName -Protocol https | Select-Object -First 1
try { $binding.RemoveSslCertificate() } catch { }   # re-run / certificate renewal
$binding.AddSslCertificate($CertThumbprint, "My")
Start-Website -Name $SiteName -ErrorAction SilentlyContinue

# --- app: listen on localhost only, trust the proxy ---------------------------
$cfgPath = Join-Path $App "config.toml"
if (-not (Test-Path $cfgPath)) { Copy-Item (Join-Path $App "config.example.toml") $cfgPath }
$cfg = Get-Content $cfgPath -Raw
$cfg = $cfg -replace '(?m)^host = "[^"]*"', 'host = "127.0.0.1"'
$cfg = $cfg -replace '(?m)^port = \d+', "port = $AppPort"
$cfg = $cfg -replace '(?m)^behind_proxy = \w+', 'behind_proxy = true'
$cfg = $cfg -replace '(?m)^secure_cookies = \w+', 'secure_cookies = true'
Set-Content -Path $cfgPath -Value $cfg -Encoding UTF8
Write-Host "config.toml: host 127.0.0.1, behind_proxy = true, secure_cookies = true"

Get-NetFirewallRule -DisplayName "MGM Network Vault (TCP *)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
foreach ($p in 80, 443) {
    $rule = "MGM Network Vault IIS (TCP $p)"
    if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $p -Action Allow -Profile Domain,Private | Out-Null
    }
}

# --- restart the app so it picks up config.toml ------------------------------
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$App*run.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $TaskName
} else {
    Write-Warning "Service task '$TaskName' not found - run deploy\install-windows.ps1 first, or restart the app yourself."
}
Start-Sleep -Seconds 8
try {
    $code = (Invoke-WebRequest "https://$HostName/login" -UseBasicParsing -TimeoutSec 15).StatusCode
    Write-Host "OK: https://$HostName answers ($code)." -ForegroundColor Green
} catch {
    Write-Warning "Could not reach https://$HostName yet: $($_.Exception.Message)"
    Write-Warning "Check DNS points $HostName to this server, and see $App\logs\service.log"
}
Write-Host "Users open: https://$HostName"
