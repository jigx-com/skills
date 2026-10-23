<#
.SYNOPSIS
  Faithful "bad connection" for Jigx device testing, on Windows.

.DESCRIPTION
  Windows/Node counterpart to bad-connection.sh (which stays the macOS path). Runs
  throttle-proxy.js on this box and points the paired device's global http_proxy at
  it over adb. The device stays CONNECTED but its traffic is slow / lossy / failing,
  which is what an offline-first app actually chokes on, unlike a clean wifi off/on.

  Why not just toggle wifi: references/universal-learnings.md records that adb
  network toggles abort BEFORE dispatch and only ever produce the recoverable
  "uploading" wedge (no active sync scope), never the in-flight PUT failure we need.
  They also blind query_device_db for the whole offline window.

  TWO RULES THAT MATTER MORE THAN ANYTHING ELSE HERE
  --------------------------------------------------
  1. Set the proxy ONCE, at the start of the session, BEFORE create_debug_session.
     Then never touch http_proxy again for the rest of the run. Every mid-run mode
     change goes through 'set', which only rewrites the control file; the running
     proxy picks it up within ~500ms with no socket, no adb, and no wifi involvement.

  2. NEVER bounce wifi. bad-connection.sh does 'svc wifi disable/enable' so the app
     re-reads the proxy; on this rig that is the most expensive action available.
     references/universal-learnings.md records the jigx_debug bridge sitting at devicePaired:false
     indefinitely afterwards, burning the rest of the run. This script never does it.

  KEEP THIS FILE PURE ASCII. PowerShell 5.1 reads a BOM-less .ps1 as cp1252, so a
  UTF-8 em-dash (E2 80 94) decodes to three chars ending in U+201D, which PowerShell
  accepts as a STRING DELIMITER. One stray em-dash silently unbalances every quoted
  string after it and the whole script fails to parse. Use ASCII punctuation only.

.EXAMPLE
  # Session start: clean pass-through, proxy wired up, nothing throttled yet.
  .\bad-connection.ps1 start -LatencyMs 0 -RateKbps 0 -LossPct 0 -JitterMs 0 -SlowHosts '<erp-host>'

.EXAMPLE
  # Slow the ERP host only, so a form PDF sits visibly in 'uploading'.
  .\bad-connection.ps1 set -RateKbps 15 -LossPct 0

.EXAMPLE
  # Hard-fail the ERP host: CONNECT answered 502 => @ctx.response.status = 0.
  .\bad-connection.ps1 set -FailHosts '<erp-host>'

.EXAMPLE
  # Deterministic mid-PUT drop instead of a dice roll.
  .\bad-connection.ps1 set -KillAfterBytes 8000

.EXAMPLE
  .\bad-connection.ps1 set -Clear      # back to clean pass-through
  .\bad-connection.ps1 status
  .\bad-connection.ps1 stop
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet('start', 'set', 'status', 'stop')]
  [string]$Command = 'status',

  [int]$Port = 8899,
  [int]$LatencyMs = 600,
  [int]$RateKbps = 50,
  [int]$LossPct = 4,
  [int]$JitterMs = 200,

  # Regex. ONLY matching CONNECT hosts get latency/rate/jitter/loss; everything else
  # is piped through untouched. Keep the jigx_debug bridge out of the blast radius.
  [string]$SlowHosts,

  # Regex. Matching hosts get an immediate 502 on CONNECT, upstream never dialled.
  [string]$FailHosts,

  # Destroy the tunnel after N bytes have flowed upstream on a throttled host.
  [int]$KillAfterBytes,

  # Reverse (default): adb reverse, proxy binds loopback only. No LAN-IP discovery,
  # no Defender inbound prompt for node.exe, works when the device is on LTE.
  # Lan: bind 0.0.0.0 and hand the device this box's LAN IP.
  [ValidateSet('Reverse', 'Lan')]
  [string]$Transport = 'Reverse',

  [switch]$Clear
)

$ErrorActionPreference = 'Stop'

# Snapshot the SCRIPT's bound parameters. Inside a function, $PSBoundParameters refers
# to that function's own parameters -- and these functions take none, so it is always
# empty there. Reading it directly inside Invoke-Start silently dropped -SlowHosts,
# which would have applied throttle/loss to EVERY host including the jigx_debug bridge:
# precisely the failure that flag exists to prevent. Capture it here, use $Bound below.
$Bound = $PSBoundParameters

$Here        = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProxyJs     = Join-Path $Here 'throttle-proxy.js'
$Rig         = Join-Path $env:TEMP 'jigx-rig'
$ControlFile = Join-Path $Rig 'throttle-control.json'
$PidFile     = Join-Path $Rig 'throttle-proxy.pid'
$LogFile     = Join-Path $Rig 'throttle-proxy.log'
$ErrFile     = Join-Path $Rig 'throttle-proxy.err.log'

if ($env:ADB) { $Adb = $env:ADB } else { $Adb = 'adb' }

function Initialize-Rig {
  if (-not (Test-Path $Rig)) { New-Item -ItemType Directory -Force $Rig | Out-Null }
}

function Get-Control {
  $h = @{}
  if (Test-Path $ControlFile) {
    try {
      $o = Get-Content $ControlFile -Raw | ConvertFrom-Json
      foreach ($p in $o.PSObject.Properties) { $h[$p.Name] = $p.Value }
    } catch { $h = @{} }
  }
  return $h
}

function Save-Control($h) {
  Initialize-Rig
  $json = $h | ConvertTo-Json -Compress
  # MUST be BOM-less: Out-File / Set-Content here default to UTF-8 WITH BOM, and a
  # leading U+FEFF makes JSON.parse throw in the proxy. The proxy treats a parse
  # failure as "keep current config", so a BOM would mean the control file silently
  # never applies -- the worst possible failure mode for this tool.
  [System.IO.File]::WriteAllText($ControlFile, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-ProxyProcess {
  if (-not (Test-Path $PidFile)) { return $null }
  $existingPid = (Get-Content $PidFile | Select-Object -First 1)
  if (-not $existingPid) { return $null }
  try { return Get-Process -Id ([int]$existingPid) -ErrorAction Stop } catch { return $null }
}

function Get-DeviceProxy {
  try { return (& $Adb shell settings get global http_proxy) } catch { return '(adb unavailable)' }
}

# Applies only the parameters the caller actually passed, so 'set -RateKbps 20' does
# not silently reset loss/latency/hosts to their defaults.
function Merge-BoundParams($h) {
  if ($Bound.ContainsKey('LatencyMs'))      { $h['latencyMs']      = $LatencyMs }
  if ($Bound.ContainsKey('RateKbps'))       { $h['rateKbps']       = $RateKbps }
  if ($Bound.ContainsKey('LossPct'))        { $h['lossPct']        = $LossPct }
  if ($Bound.ContainsKey('JitterMs'))       { $h['jitterMs']       = $JitterMs }
  if ($Bound.ContainsKey('KillAfterBytes')) { $h['killAfterBytes'] = $KillAfterBytes }
  if ($Bound.ContainsKey('SlowHosts'))      { $h['slowHosts']      = $SlowHosts }
  if ($Bound.ContainsKey('FailHosts'))      { $h['failHosts']      = $FailHosts }
  return $h
}

function Invoke-Start {
  Initialize-Rig
  if (-not (Test-Path $ProxyJs)) { throw "throttle-proxy.js not found at $ProxyJs" }

  $running = Get-ProxyProcess
  if ($running) {
    Write-Host "proxy already running (pid $($running.Id)). Use 'set' to change modes, or 'stop' first."
  } else {
    # Seed the control file from the start parameters: one source of truth, and it
    # means 'set' afterwards is a pure merge over what start established.
    $h = @{
      latencyMs = $LatencyMs; rateKbps = $RateKbps
      lossPct   = $LossPct;   jitterMs = $JitterMs
    }
    if ($Bound.ContainsKey('SlowHosts'))      { $h['slowHosts']      = $SlowHosts }
    if ($Bound.ContainsKey('FailHosts'))      { $h['failHosts']      = $FailHosts }
    if ($Bound.ContainsKey('KillAfterBytes')) { $h['killAfterBytes'] = $KillAfterBytes }
    Save-Control $h

    if ($Transport -eq 'Reverse') { $bind = '127.0.0.1' } else { $bind = '0.0.0.0' }

    $env:PORT         = "$Port"
    $env:BIND         = $bind
    $env:CONTROL_FILE = $ControlFile

    $argList = '"' + $ProxyJs + '"'
    $proc = Start-Process node -ArgumentList $argList -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile -WindowStyle Hidden -PassThru
    $proc.Id | Set-Content $PidFile -Encoding utf8
    Start-Sleep -Milliseconds 700
    $check = Get-ProxyProcess
    if (-not $check) { throw "proxy failed to start. See $ErrFile" }
    Write-Host "proxy started (pid $($proc.Id)) bound ${bind}:${Port}"
  }

  if ($Transport -eq 'Reverse') {
    & $Adb reverse "tcp:$Port" "tcp:$Port" | Out-Null
    $target = "127.0.0.1:$Port"
  } else {
    $route = Find-NetRoute -RemoteIPAddress 8.8.8.8 | Select-Object -First 1
    $ip = $route.IPAddress
    if (-not $ip) { throw 'could not determine this box LAN IP' }
    $target = "${ip}:${Port}"
  }

  & $Adb shell settings put global http_proxy $target
  Write-Host "device http_proxy -> $target  (transport: $Transport)"
  Write-Host "proxy log: $LogFile"
  Write-Host ''
  Write-Host 'GO/NO-GO before testing anything. A silent bypass invalidates every later observation:'
  Write-Host "  1. adb shell settings get global http_proxy   -> expect $target"
  Write-Host '  2. adb shell am start -a android.intent.action.VIEW -d https://example.com'
  Write-Host '     -> expect a "CONNECT example.com:443" line in the proxy log.'
  Write-Host '        Absent => transport is broken (firewall / adb reverse). Fix that first.'
  Write-Host '  3. In the Jigx app, force an Acumatica sync -> expect CONNECT <erp host>:443.'
  Write-Host '        Step 2 logs but step 3 does not => the app bypasses the system proxy;'
  Write-Host '        fall back to repointing the ERP base URL config at a dead host (see README).'
}

function Invoke-Set {
  $running = Get-ProxyProcess
  if (-not $running) { Write-Host 'warning: no proxy running. Writing the control file anyway.' }

  if ($Clear) {
    # Explicit permissive baseline. slowHosts/failHosts omitted => the proxy resets
    # them to its defaults (match-all / none), so this really is a full clear.
    $h = @{ latencyMs = 0; rateKbps = 0; lossPct = 0; jitterMs = 0; killAfterBytes = 0 }
  } else {
    $h = Merge-BoundParams (Get-Control)
  }
  Save-Control $h
  Write-Host "control updated: $($h | ConvertTo-Json -Compress)"
  Write-Host '(the running proxy re-reads this within ~500ms: no restart, no adb, no wifi bounce)'
}

function Invoke-Status {
  $running = Get-ProxyProcess
  if ($running) {
    Write-Host "proxy: running (pid $($running.Id)) on port $Port"
  } else {
    Write-Host 'proxy: not running'
  }
  Write-Host "device http_proxy: $(Get-DeviceProxy)"
  try {
    $rev = & $Adb reverse --list
    if ($rev) { Write-Host "adb reverse: $rev" } else { Write-Host 'adb reverse: (none)' }
  } catch { Write-Host 'adb reverse: (adb unavailable)' }
  if (Test-Path $ControlFile) {
    Write-Host "control: $(Get-Content $ControlFile -Raw)"
  } else {
    Write-Host 'control: (no control file)'
  }
  if (Test-Path $LogFile) {
    Write-Host "--- last 12 proxy log lines ($LogFile) ---"
    Get-Content $LogFile -Tail 12
  }
}

function Invoke-Stop {
  try { & $Adb shell settings put global http_proxy :0 } catch { }
  try { & $Adb shell settings delete global global_http_proxy_host } catch { }
  try { & $Adb reverse --remove "tcp:$Port" } catch { }

  $running = Get-ProxyProcess
  if ($running) {
    Stop-Process -Id $running.Id -Force -Confirm:$false
    Write-Host "proxy stopped (pid $($running.Id))"
  } else {
    Write-Host 'no proxy running'
  }
  if (Test-Path $PidFile) { Remove-Item $PidFile -Force -Confirm:$false }
  Write-Host "device http_proxy now: $(Get-DeviceProxy)"
}

switch ($Command) {
  'start'  { Invoke-Start }
  'set'    { Invoke-Set }
  'status' { Invoke-Status }
  'stop'   { Invoke-Stop }
}
