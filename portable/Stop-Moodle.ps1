$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Runtime = Join-Path $Root 'runtime'
$StatePath = Join-Path $Runtime 'processes.json'
if (-not (Test-Path $StatePath)) { Write-Host 'Moodle is already stopped.'; exit 0 }
$state = Get-Content $StatePath -Raw | ConvertFrom-Json
foreach ($item in @(@{Id=$state.web; Started=$state.webStarted; File=(Join-Path $Root 'server\php\php.exe')}, @{Id=$state.database; Started=$state.databaseStarted; File=(Join-Path $Root 'server\mysql\bin\mysqld.exe')})) {
    if (-not $item.Id) { continue }
    $process = Get-Process -Id $item.Id -ErrorAction SilentlyContinue
    if (-not $process -or $process.Path -ne $item.File -or $process.StartTime.ToUniversalTime().ToString('o') -ne $item.Started) { continue }
    if ($item.File.EndsWith('mysqld.exe')) {
        # Use the database's native shutdown command for a clean InnoDB stop.
        & (Join-Path $Root 'server\php\php.exe') '-c' (Join-Path $Runtime 'php.ini') (Join-Path $Root 'shutdown.php')
        if ($LASTEXITCODE -ne 0) { throw 'Clean database shutdown failed. See runtime\logs.' }
        $process.WaitForExit(30000) | Out-Null
        if (-not $process.HasExited) { throw 'The database is still shutting down. Wait and run Stop-Moodle.cmd again.' }
    } else { Stop-Process -Id $process.Id }
}
Remove-Item $StatePath
Write-Host 'Moodle stopped. Your course and work are saved.'
