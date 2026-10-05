# Optional, separately installed ELTDX 3.2.2 personal research gateway.
param(
    [string]$GatewayVenv = (Join-Path (Split-Path -Parent $PSScriptRoot) '.eltdx-venv'),
    [ValidateRange(1024, 65535)][int]$Port = 3022
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$executable = Join-Path $GatewayVenv 'Scripts\python.exe'
$runtime = Join-Path $project 'local-runtime\eltdx'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw 'Install eltdx[http]==3.2.2 in a separate .eltdx-venv first; see docs/eltdx-gateway.md.'
}

Add-Type -AssemblyName System.Net.Http
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.UseProxy = $false
$http = New-Object System.Net.Http.HttpClient($handler)
$http.Timeout = [TimeSpan]::FromSeconds(2)
function Test-EltdxHealth {
    try {
        $response = $http.GetAsync("http://127.0.0.1:$Port/health").GetAwaiter().GetResult()
        try {
            if (-not $response.IsSuccessStatusCode) { return $false }
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
            return ($body.ok -eq $true -and $body.service -eq 'eltdx' -and $body.version -eq '3.2.2')
        } finally { $response.Dispose() }
    } catch { return $false }
}

try {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        if (-not (Test-EltdxHealth)) { throw "Port $Port belongs to a different or incompatible service; no process was stopped." }
        Write-Output "ELTDX research gateway is already running on 127.0.0.1:$Port."
        return
    }
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $gatewayProcess = Start-Process -FilePath $executable -ArgumentList @(
        '-m', 'eltdx.http_server', '--host', '127.0.0.1', '--port', "$Port", '--timeout', '2',
        '--pool-size', '2', '--server-count', '2', '--connections-per-server', '1', '--log-level', 'warning'
    ) -WorkingDirectory $project -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtime "gateway-$Port.out.log") `
        -RedirectStandardError (Join-Path $runtime "gateway-$Port.err.log")
    Set-Content -LiteralPath (Join-Path $runtime "gateway-$Port.pid") -Value $gatewayProcess.Id
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (Test-EltdxHealth) {
            Write-Output "Started ELTDX research gateway on 127.0.0.1:$Port (launcher PID $($gatewayProcess.Id)). Health checks the HTTP process; use the panel trial to check market data."
            return
        }
        $gatewayProcess.Refresh()
        if ($gatewayProcess.HasExited) { break }
        Start-Sleep -Milliseconds 250
    }
    # Stop only this invocation's process and its direct Python child, if any.
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($gatewayProcess.Id)" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        if ($child.Name -eq 'python.exe' -and $child.CommandLine -like '*eltdx.http_server*') {
            Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        }
    }
    $gatewayProcess.Refresh()
    if (-not $gatewayProcess.HasExited) { Stop-Process -Id $gatewayProcess.Id -ErrorAction SilentlyContinue }
    throw "ELTDX gateway did not become ready. Inspect local-runtime/eltdx/gateway-$Port.err.log; the panel was not started by this script."
} finally {
    $http.Dispose()
    $handler.Dispose()
}
