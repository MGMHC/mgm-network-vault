<#
  MGM Network Vault - publish the app through IIS with HTTPS (Cloudflare Origin Certificate support).

  Prerequisites:
    * The app is running on port 8080 (via NSSM service "NetworkVault" or scheduled task)
    * IIS modules installed: URL Rewrite 2.1 and Application Request Routing 3.0 (already installed)
    * Cloudflare Origin certificate files in C:\Certs or imported into Cert:\LocalMachine\My

  Usage (in an elevated "Run as Administrator" PowerShell prompt):
      cd c:\MGM\mgm-network-vault
      powershell -ExecutionPolicy Bypass -File deploy\iis\setup-iis.ps1

  Optional parameters:
      -HostName "networkvault.mgmhealthcare.in"
      -CertThumbprint "F8B88429298FB5FAF50C0F98DB4C7B292172D221"
      -PfxPath "C:\Certs\mgm-origin.pfx"
      -PfxPassword "MGM@NetworkVault2026!"
#>
param(
    [string]$HostName = "networkvault.mgmhealthcare.in",
    [string]$CertThumbprint = "F8B88429298FB5FAF50C0F98DB4C7B292172D221",
    [string]$PfxPath = "C:\Certs\mgm-origin.pfx",
    [string]$PfxPassword = "MGM@NetworkVault2026!",
    [string]$SiteName = "MGM Network Vault",
    [string]$SitePath = "C:\inetpub\network-vault",
    [int]$AppPort = 8080,
    [string]$ServiceName = "NetworkVault",
    [string]$TaskName = "MGM Network Vault"
)
$ErrorActionPreference = "Stop"
$App = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$appcmd = "$env:windir\System32\inetsrv\appcmd.exe"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in PowerShell started with 'Run as Administrator'."
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Setting up IIS Reverse Proxy for MGM Network Vault" -ForegroundColor Cyan
Write-Host " HostName: $HostName" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

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

# --- certificate setup & auto-import -----------------------------------------
Write-Host "`n[1/6] Configuring TLS Certificate..." -ForegroundColor Yellow

# 1. Cloudflare Root CA import if present (so Windows marks origin cert as trusted)
$caRootPath = "C:\Certs\cloudflare_origin_rsa_root.pem"
if (Test-Path $caRootPath) {
    try {
        Write-Host "Importing Cloudflare Origin Root CA into Trusted Root Certification Authorities..."
        Import-Certificate -FilePath $caRootPath -CertStoreLocation "Cert:\LocalMachine\Root" -ErrorAction SilentlyContinue | Out-Null
    } catch {
        Write-Warning "Could not import Cloudflare Root CA: $($_.Exception.Message)"
    }
}

# 2. Check or create PFX if needed
$keyPath = "C:\Certs\mgm-origin.key"
$pemPath = "C:\Certs\mgm-origin.pem"
if ((-not (Test-Path $PfxPath)) -and (Test-Path $keyPath) -and (Test-Path $pemPath)) {
    $openssl = "openssl"
    if (Test-Path "C:\Program Files\Git\usr\bin\openssl.exe") {
        $openssl = "C:\Program Files\Git\usr\bin\openssl.exe"
    }
    Write-Host "Generating PFX package from $pemPath and $keyPath..."
    if (Test-Path $caRootPath) {
        & $openssl pkcs12 -export -out $PfxPath -inkey $keyPath -in $pemPath -certfile $caRootPath -passout "pass:$PfxPassword"
    } else {
        & $openssl pkcs12 -export -out $PfxPath -inkey $keyPath -in $pemPath -passout "pass:$PfxPassword"
    }
}

# 3. Import PFX into Cert:\LocalMachine\My if thumbprint not provided or cert missing
if ([string]::IsNullOrWhiteSpace($CertThumbprint) -or (-not (Get-Item "Cert:\LocalMachine\My\$CertThumbprint" -ErrorAction SilentlyContinue))) {
    if (Test-Path $PfxPath) {
        Write-Host "Importing $PfxPath into Cert:\LocalMachine\My..."
        $secPass = ConvertTo-SecureString $PfxPassword -AsPlainText -Force
        $imported = Import-PfxCertificate -FilePath $PfxPath -CertStoreLocation "Cert:\LocalMachine\My" -Password $secPass -Exportable
        $CertThumbprint = $imported.Thumbprint
        Write-Host "Imported certificate Thumbprint: $CertThumbprint" -ForegroundColor Green
    }
}

$CertThumbprint = ($CertThumbprint -replace '[^0-9A-Fa-f]', '').ToUpper()
$cert = Get-Item "Cert:\LocalMachine\My\$CertThumbprint" -ErrorAction SilentlyContinue
if (-not $cert) {
    throw "Certificate '$CertThumbprint' not found in Cert:\LocalMachine\My. Please import $PfxPath first."
}
Write-Host "Certificate validated: $($cert.Subject)" -ForegroundColor Green
Write-Host "Thumbprint: $($cert.Thumbprint)"
Write-Host "Expires: $($cert.NotAfter)"
if ($cert.NotAfter -lt (Get-Date).AddDays(30)) { Write-Warning "Certificate expires on $($cert.NotAfter) - renew it soon." }

# --- ARR proxy settings (server level) ---------------------------------------
Write-Host "`n[2/6] Configuring Application Request Routing (ARR)..." -ForegroundColor Yellow
& $appcmd set config -section:system.webServer/proxy /enabled:"True" /preserveHostHeader:"True" `
    /timeout:"00:15:00" /responseBufferLimit:"0" /reverseRewriteHostInResponseHeaders:"False" `
    /includePortInXForwardedFor:"False" /commit:apphost | Out-Null

foreach ($var in "HTTP_X_FORWARDED_PROTO", "HTTP_X_FORWARDED_HOST") {
    $existing = & $appcmd list config -section:system.webServer/rewrite/allowedServerVariables
    if (-not ($existing -match $var)) {
        & $appcmd set config -section:system.webServer/rewrite/allowedServerVariables /+"[name='$var']" /commit:apphost | Out-Null
    }
}

# --- site configuration ------------------------------------------------------
Write-Host "`n[3/6] Configuring IIS Site '$SiteName'..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force $SitePath | Out-Null
Copy-Item "$PSScriptRoot\web.config" "$SitePath\web.config" -Force

# Create or update site
if (-not (Get-Website -Name $SiteName -ErrorAction SilentlyContinue)) {
    Write-Host "Creating IIS site '$SiteName'..."
    New-Website -Name $SiteName -PhysicalPath $SitePath -HostHeader $HostName -Port 80 | Out-Null
} else {
    Set-ItemProperty "IIS:\Sites\$SiteName" -Name physicalPath -Value $SitePath
}

# Ensure HTTP binding on port 80
if (-not (Get-WebBinding -Name $SiteName -Protocol http -Port 80 -HostHeader $HostName -ErrorAction SilentlyContinue)) {
    New-WebBinding -Name $SiteName -Protocol http -Port 80 -HostHeader $HostName
}

# Ensure HTTPS binding on port 443 with SNI
$httpsBinding = Get-WebBinding -Name $SiteName -Protocol https -Port 443 -HostHeader $HostName -ErrorAction SilentlyContinue
if (-not $httpsBinding) {
    New-WebBinding -Name $SiteName -Protocol https -Port 443 -HostHeader $HostName -SslFlags 1
    $httpsBinding = Get-WebBinding -Name $SiteName -Protocol https -Port 443 -HostHeader $HostName
}

# Bind SSL certificate
try {
    $httpsBinding.RemoveSslCertificate()
} catch { }

try {
    $httpsBinding.AddSslCertificate($CertThumbprint, "My")
} catch {
    Write-Warning "WebBinding.AddSslCertificate reported: $($_.Exception.Message). Applying via netsh fallback..."
    & netsh http delete sslcert hostnameport="${HostName}:443" 2>$null | Out-Null
    $guid = [Guid]::NewGuid().ToString("B")
    & netsh http add sslcert hostnameport="${HostName}:443" certhash=$CertThumbprint certstorename=MY appid="$guid"
}

# Start website
Start-Website -Name $SiteName -ErrorAction SilentlyContinue
Write-Host "IIS Site '$SiteName' configured and started." -ForegroundColor Green

# --- app: listen on localhost only, trust the proxy ---------------------------
Write-Host "`n[4/6] Updating config.toml..." -ForegroundColor Yellow
$cfgPath = Join-Path $App "config.toml"
if (-not (Test-Path $cfgPath)) { Copy-Item (Join-Path $App "config.example.toml") $cfgPath }
$cfg = Get-Content $cfgPath -Raw
$cfg = $cfg -replace '(?m)^host = "[^"]*"', 'host = "127.0.0.1"'
$cfg = $cfg -replace '(?m)^port = \d+', "port = $AppPort"
$cfg = $cfg -replace '(?m)^behind_proxy = \w+', 'behind_proxy = true'
$cfg = $cfg -replace '(?m)^secure_cookies = \w+', 'secure_cookies = true'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($cfgPath, $cfg, $utf8NoBom)
Write-Host "config.toml: host = '127.0.0.1', behind_proxy = true, secure_cookies = true" -ForegroundColor Green

# --- firewall rules ----------------------------------------------------------
Write-Host "`n[5/6] Updating Windows Firewall..." -ForegroundColor Yellow
Get-NetFirewallRule -DisplayName "MGM Network Vault (TCP *)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
foreach ($p in 80, 443) {
    $rule = "MGM Network Vault IIS (TCP $p)"
    if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $p -Action Allow -Profile Domain,Private | Out-Null
    }
}

# --- restart the app service -------------------------------------------------
Write-Host "`n[6/6] Restarting Application Service..." -ForegroundColor Yellow
$restarted = $false

# 1. NSSM / Windows Service NetworkVault
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "Restarting Windows service '$ServiceName'..."
    try {
        Restart-Service -Name $ServiceName -Force -ErrorAction Stop
    } catch {
        Start-Sleep -Seconds 2
        Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
    }
    $restarted = $true
} elseif (Get-Service -Name "MGMNetworkVault" -ErrorAction SilentlyContinue) {
    Write-Host "Restarting Windows service 'MGMNetworkVault'..."
    try {
        Restart-Service -Name "MGMNetworkVault" -Force -ErrorAction Stop
    } catch {
        Start-Sleep -Seconds 2
        Start-Service -Name "MGMNetworkVault" -ErrorAction SilentlyContinue
    }
    $restarted = $true
}

# 2. Scheduled Task fallback
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Restarting Scheduled Task '$TaskName'..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$App*run.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $TaskName
    $restarted = $true
}

if (-not $restarted) {
    Write-Warning "Could not find a service or task named '$ServiceName' or '$TaskName'."
    Write-Warning "Please restart your app service manually."
}

Write-Host "Waiting for application to warm up on port $AppPort..."
Start-Sleep -Seconds 4

# Test local backend
try {
    $backendRes = Invoke-WebRequest -Uri "http://127.0.0.1:${AppPort}/login" -UseBasicParsing -TimeoutSec 10
    Write-Host "Local backend responding: HTTP $($backendRes.StatusCode)" -ForegroundColor Green
} catch {
    Write-Warning "Local backend test on port ${AppPort}: $($_.Exception.Message)"
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host " SETUP COMPLETE!" -ForegroundColor Green
Write-Host " Site: https://$HostName" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Cloudflare Settings Reminder:"
Write-Host "  1. DNS: Ensure 'networking' A or CNAME record is PROXIED (Orange Cloud)."
Write-Host "  2. SSL/TLS: Mode MUST be set to 'Full' or 'Full (strict)'."
Write-Host "  3. Inbound: Ensure port 443 is forwarded through your firewall/router to this server."
