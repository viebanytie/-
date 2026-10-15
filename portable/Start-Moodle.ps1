param([switch]$NoBrowser, [switch]$Background)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Runtime = Join-Path $Root 'runtime'
$PhpDir = Join-Path $Root 'server\php'
$Php = Join-Path $PhpDir 'php.exe'
$MysqlDir = Join-Path $Root 'server\mysql'
$Database = Join-Path $MysqlDir 'bin\mysqld.exe'
$Url = 'http://127.0.0.1:8088'
if (-not [Environment]::Is64BitOperatingSystem) { throw 'This package requires Windows 10 x64.' }
foreach ($name in @('', 'logs', 'temp', 'sessions', 'moodledata')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Runtime $name) | Out-Null
}
$lock = [IO.File]::Open((Join-Path $Runtime 'launcher.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
function Test-Port([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try { $client.Connect('127.0.0.1', $Port); return $true } catch { return $false } finally { $client.Dispose() }
}
function Quote([string]$Value) { return '"' + $Value + '"' }
function Run-Php([string]$Script, [string[]]$Extra = @()) {
    Push-Location $PhpDir
    try {
        & $Php '-c' (Join-Path $Runtime 'php.ini') (Join-Path $Root $Script) @Extra
        if ($LASTEXITCODE -ne 0) { throw "PHP step failed: $Script. See runtime\logs." }
    } finally { Pop-Location }
}
function New-Password {
    $bytes = New-Object byte[] 20
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return 'A9!' + ([Convert]::ToBase64String($bytes) -replace '[+/=]', 'x')
}
try {
    if (Test-Port 8088) {
        $statePath = Join-Path $Runtime 'processes.json'
        $ours = $false
        if (Test-Path $statePath) {
            $state = Get-Content $statePath -Raw | ConvertFrom-Json
            $existing = Get-Process -Id $state.web -ErrorAction SilentlyContinue
            $ours = $existing -and ($existing.Path -eq $Php) -and ($existing.StartTime.ToUniversalTime().ToString('o') -eq $state.webStarted)
        }
        if ($ours) { if (-not $NoBrowser) { Start-Process "$Url/course/view.php?id=$($state.course)" }; return }
        throw 'Port 8088 is already used by another application. Close that application and start again.'
    }
    if (Test-Port 3308) { throw 'Port 3308 is already in use. Run Stop-Moodle.cmd for this kit before starting it again.' }
    $configPath = Join-Path $Runtime 'local.json'
    if (-not (Test-Path $configPath)) {
        $local = @{ root=(New-Password); database=(New-Password); admin=(New-Password); student=(New-Password) }
        $local | ConvertTo-Json | Set-Content -Encoding UTF8 $configPath
        # Strip the BOM so JSON is also readable by PHP.
        [IO.File]::WriteAllText($configPath, ($local | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
        $login = "MOODLE: $Url`r`n`r`nAdministrator`r`nLogin: admin`r`nPassword: $($local.admin)`r`n`r`nStudent`r`nLogin: student`r`nPassword: $($local.student)`r`n"
        [IO.File]::WriteAllText((Join-Path $Root 'LOGIN.txt'), $login, (New-Object Text.UTF8Encoding($true)))
    }
    $ini = Get-Content (Join-Path $Root 'php.ini.template') -Raw
    $ini = $ini.Replace('{EXT_DIR}', ((Join-Path $PhpDir 'ext') -replace '\\','/')).Replace('{SESSION_DIR}', ((Join-Path $Runtime 'sessions') -replace '\\','/')).Replace('{TEMP_DIR}', ((Join-Path $Runtime 'temp') -replace '\\','/')).Replace('{ERROR_LOG}', ((Join-Path $Runtime 'logs\php-errors.log') -replace '\\','/'))
    [IO.File]::WriteAllText((Join-Path $Runtime 'php.ini'), $ini, (New-Object Text.UTF8Encoding($false)))
    Write-Host 'Starting the bundled database...'
    $dbArgs = @('--no-defaults', ('--basedir='+(Quote ($MysqlDir -replace '\\','/'))), ('--datadir='+(Quote ((Join-Path $MysqlDir 'data') -replace '\\','/'))), ('--plugin-dir='+(Quote ((Join-Path $MysqlDir 'lib\plugin') -replace '\\','/'))), ('--tmpdir='+(Quote ((Join-Path $Runtime 'temp') -replace '\\','/'))), '--port=3308', '--bind-address=127.0.0.1', '--skip-name-resolve', '--character-set-server=utf8mb4', '--collation-server=utf8mb4_unicode_ci', '--innodb-buffer-pool-size=64M', '--console')
    $db = Start-Process $Database -ArgumentList $dbArgs -WorkingDirectory $MysqlDir -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Runtime 'logs\database.log') -RedirectStandardError (Join-Path $Runtime 'logs\database-errors.log')
    $state = @{ database=$db.Id; databaseStarted=$db.StartTime.ToUniversalTime().ToString('o'); web=0; webStarted=''; course=2 }
    $state | ConvertTo-Json | Set-Content (Join-Path $Runtime 'processes.json')
    $ready = $false
    Push-Location $PhpDir
    try {
        for ($i=0; $i -lt 90; $i++) {
            & $Php '-c' (Join-Path $Runtime 'php.ini') (Join-Path $Root 'database.php') 'ping' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { $ready=$true; break }
            if ($db.HasExited) { throw 'The database could not start. See runtime\logs\database-errors.log.' }
            Start-Sleep -Seconds 1
        }
    } finally { Pop-Location }
    if (-not $ready) { throw 'Database startup timed out. See runtime\logs.' }
    Run-Php 'database.php'
    Push-Location $PhpDir
    try { & $Php '-c' (Join-Path $Runtime 'php.ini') (Join-Path $Root 'status.php') 2>$null | Out-Null; $installed=($LASTEXITCODE -eq 0) } finally { Pop-Location }
    if (-not $installed) {
        Write-Host 'Preparing Moodle automatically. First start can take several minutes...'
        Run-Php 'install-db.php' *> (Join-Path $Runtime 'logs\installation.log')
    }
    if (-not (Test-Path (Join-Path $Runtime 'ready'))) {
        Write-Host 'Loading the prepared course, quiz and practice...'
        Run-Php 'bootstrap.php' *> (Join-Path $Runtime 'logs\course-setup.log')
    }
    $course = Get-Content (Join-Path $Runtime 'course.json') -Raw | ConvertFrom-Json
    $state.course = $course.courseid
    $webArgs = @('-c', (Quote (Join-Path $Runtime 'php.ini')), '-S', '127.0.0.1:8088', '-t', (Quote (Join-Path $Root 'server\moodle')), (Quote (Join-Path $Root 'router.php')))
    $web = Start-Process $Php -ArgumentList $webArgs -WorkingDirectory $PhpDir -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Runtime 'logs\web.log') -RedirectStandardError (Join-Path $Runtime 'logs\web-errors.log')
    $state.web=$web.Id; $state.webStarted=$web.StartTime.ToUniversalTime().ToString('o')
    $state | ConvertTo-Json | Set-Content (Join-Path $Runtime 'processes.json')
    for ($i=0; $i -lt 30 -and -not (Test-Port 8088); $i++) { Start-Sleep -Seconds 1 }
    $response = Invoke-WebRequest "$Url/login/index.php" -UseBasicParsing -TimeoutSec 120
    if ($response.StatusCode -ne 200) { throw 'Moodle did not return a working login page.' }
    Write-Host "Moodle is ready: $Url. Your login and password are in LOGIN.txt."
    if (-not $NoBrowser) {
        try { Start-Process (Join-Path $Root 'LOGIN.txt'); Start-Process "$Url/course/view.php?id=$($course.courseid)" }
        catch { Write-Host "Open $Url in your browser and LOGIN.txt in Notepad." }
    }
    if (-not $Background) {
        Write-Host 'Leave this window open while using Moodle. To finish, run Stop-Moodle.cmd.'
        while (-not $web.HasExited -or -not $db.HasExited) { Start-Sleep -Seconds 1 }
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host 'Use Stop-Moodle.cmd, then inspect runtime\logs and try Start-Moodle.cmd again.'
    exit 1
} finally { $lock.Dispose() }
