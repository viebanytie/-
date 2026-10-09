[CmdletBinding()]
param([ValidateSet('audit','files','defender_quick','defender_full')][string]$Mode='audit', [string]$RequestFile)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$warnings = New-Object 'System.Collections.Generic.List[string]'
$result = [ordered]@{mode=$Mode;collected_at=[DateTimeOffset]::Now.ToString('o');warnings=@()}

function Read-Defender {
    $data = @{ available=$false; threats=@(); exclusions_paths=@(); exclusions_processes=@() }
    try {
        $status = Get-MpComputerStatus
        $data.available = $true
        $data.status = @{
            antivirus_enabled=[bool]$status.AntivirusEnabled; realtime_enabled=[bool]$status.RealTimeProtectionEnabled
            running_mode=[string]$status.AMRunningMode; signature_age=[int]$status.AntivirusSignatureAge
            signature_version=[string]$status.AntivirusSignatureVersion
            quick_start=[string]$status.QuickScanStartTime; quick_end=[string]$status.QuickScanEndTime
            full_start=[string]$status.FullScanStartTime; full_end=[string]$status.FullScanEndTime
        }
    } catch { $warnings.Add('Статус Defender: ' + $_.ToString()) }
    try {
        $data.threats = @(Get-MpThreat | ForEach-Object {
            @{name=[string]$_.ThreatName;id=[string]$_.ThreatID;active=[bool]$_.IsActive
              executed=[bool]$_.DidThreatExecute;resources=@($_.Resources)}
        })
    } catch { $warnings.Add('Угрозы Defender: ' + $_.ToString()) }
    try {
        $preferences = Get-MpPreference
        $data.exclusions_paths = @($preferences.ExclusionPath | Where-Object { $_ })
        $data.exclusions_processes = @($preferences.ExclusionProcess | Where-Object { $_ })
    } catch { $warnings.Add('Исключения Defender: ' + $_.ToString()) }
    return $data
}

function Audit-Computer {
    $processes = New-Object 'System.Collections.Generic.List[object]'
    $baseline = @{}
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $cores = [Environment]::ProcessorCount
    $unreadable = 0
    try {
        foreach ($p in @(Get-CimInstance -ClassName Win32_Process)) {
            $item = @{pid=[int]$p.ProcessId;name=[string]$p.Name;path=[string]$p.ExecutablePath
                      command=[string]$p.CommandLine;cpu_percent=$null}
            $processes.Add($item)
            if ($p.ProcessId -gt 4) {
                if (-not $p.ExecutablePath -or -not $p.CommandLine) { $unreadable++ }
                try {
                    $live = Get-Process -Id $p.ProcessId
                    $baseline[[int]$p.ProcessId] = @{cpu=[double]$live.CPU;time=$timer.Elapsed.TotalSeconds;started=$live.StartTime}
                } catch { }
            }
        }
        Start-Sleep -Seconds 5
        foreach ($item in $processes) {
            if (-not $baseline.ContainsKey($item.pid)) { continue }
            try {
                $live = Get-Process -Id $item.pid
                $before = $baseline[$item.pid]
                if ($live.StartTime -ne $before.started) { continue }
                $elapsed = $timer.Elapsed.TotalSeconds - $before.time
                $value = 100 * ([double]$live.CPU - $before.cpu) / ($elapsed * $cores)
                $item.cpu_percent = [Math]::Round([Math]::Max(0,[Math]::Min(100,$value)),2)
            } catch { }
        }
    } catch { $warnings.Add('Процессы/CPU: ' + $_.ToString()) }
    if ($unreadable) { $warnings.Add(('У {0} процессов недоступны путь или аргументы. Для полного доступа нужны права администратора.' -f $unreadable)) }
    $result.processes = @($processes.ToArray())
    try {
        $result.gpu = @(Get-CimInstance -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine | ForEach-Object {
            if ($_.Name -match 'pid_(\d+)') { @{pid=[int]$Matches[1];engine=[string]$_.Name;percent=[double]$_.UtilizationPercentage} }
        })
    } catch { $result.gpu=@(); $warnings.Add('GPU: счётчики WDDM недоступны: ' + $_.ToString()) }
    try {
        $result.connections = @(Get-NetTCPConnection -State Established | ForEach-Object {
            @{pid=[int]$_.OwningProcess;address=[string]$_.RemoteAddress;port=[int]$_.RemotePort}
        })
    } catch { $result.connections=@(); $warnings.Add('Сетевые подключения: ' + $_.ToString()) }
    try {
        $result.dns = @(Get-DnsClientCache | ForEach-Object {
            $name = [string]$_.Entry
            if (-not $name) { $name = [string]$_.Name }
            @{name=$name;data=[string]$_.Data}
        })
    } catch { $result.dns=@(); $warnings.Add('Кэш DNS: ' + $_.ToString()) }
    $persistence = New-Object 'System.Collections.Generic.List[object]'
    try {
        $taskErrors = @()
        foreach ($task in @(Get-ScheduledTask -ErrorAction SilentlyContinue -ErrorVariable taskErrors)) {
            foreach ($action in $task.Actions) {
                if ($action.Execute) {
                    $persistence.Add(@{name=$task.TaskPath+$task.TaskName;source='Все задания планировщика'
                        command=('"'+$action.Execute+'" '+$action.Arguments);enabled=($task.Settings.Enabled -ne $false)})
                }
            }
        }
        foreach ($errorRecord in $taskErrors) { $warnings.Add('Планировщик: ' + $errorRecord.ToString()) }
    } catch { $warnings.Add('Все задания планировщика: ' + $_.ToString()) }
    foreach ($class in @('CommandLineEventConsumer','ActiveScriptEventConsumer')) {
        try {
            foreach ($consumer in @(Get-CimInstance -Namespace root/subscription -ClassName $class)) {
                $command = [string]$consumer.CommandLineTemplate
                if (-not $command) { $command = [string]$consumer.ScriptText }
                if (-not $command) { $command = [string]$consumer.ScriptFileName }
                $persistence.Add(@{name=[string]$consumer.Name;source='WMI '+$class;command=$command;enabled=$null})
            }
        } catch { $warnings.Add(('WMI {0}: {1}' -f $class, $_.ToString())) }
    }
    $base = $null
    try {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Default)
        foreach ($branch in @('SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options',
                              'SOFTWARE\Microsoft\Windows NT\CurrentVersion\SilentProcessExit')) {
            $parent = $base.OpenSubKey($branch)
            if (-not $parent) { continue }
            try {
                foreach ($name in $parent.GetSubKeyNames()) {
                    $key = $null
                    try {
                        $key = $parent.OpenSubKey($name)
                        foreach ($value in @('Debugger','MonitorProcess')) {
                            $command = [string]$key.GetValue($value)
                            if ($command) { $persistence.Add(@{name=$name;source='Реестр '+$value;command=$command;enabled=$null}) }
                        }
                    } catch { $warnings.Add('IFEO/SilentProcessExit ' + $name + ': ' + $_.ToString()) }
                    finally { if ($key) { $key.Close() } }
                }
            } finally { $parent.Close() }
        }
    } catch { $warnings.Add('IFEO/SilentProcessExit: ' + $_.ToString()) }
    finally { if ($base) { $base.Close() } }
    $result.persistence = @($persistence.ToArray())
    $result.defender = Read-Defender
}

function Inspect-Files {
    $request = ConvertFrom-Json ([IO.File]::ReadAllText($RequestFile,[Text.Encoding]::UTF8))
    $files = New-Object 'System.Collections.Generic.List[object]'
    $paths = @($request.paths | Select-Object -Unique)
    if ($paths.Count -gt 100) { $warnings.Add('Метаданные ограничены 100 файлами; часть кандидатов не проверена.'); $paths=@($paths | Select-Object -First 100) }
    foreach ($inputPath in $paths) {
        $path = [Environment]::ExpandEnvironmentVariables([string]$inputPath)
        if ($path.StartsWith('\SystemRoot',[StringComparison]::OrdinalIgnoreCase)) { $path=$env:SystemRoot+$path.Substring(11) }
        $row = @{path=[string]$inputPath;signature='Недоступна';signer='';sha256=''}
        try {
            $file = Get-Item -LiteralPath $path
            if ($file.PSIsContainer) { throw 'Путь относится к каталогу.' }
            $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
            $row.signature = [string]$signature.Status
            if ($signature.SignerCertificate) { $row.signer=[string]$signature.SignerCertificate.Subject }
            $row.sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
        } catch { $warnings.Add('Файл ' + $path + ': ' + $_.ToString()) }
        $files.Add($row)
    }
    $result.files = @($files.ToArray())
}

function Scan-Defender {
    $scanType = 1
    if ($Mode -eq 'defender_full') { $scanType=2 }
    $tool = $null
    $platform = Join-Path $env:ProgramData 'Microsoft\Windows Defender\Platform'
    if (Test-Path -LiteralPath $platform) {
        $tool = Get-ChildItem -LiteralPath $platform -Directory | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'MpCmdRun.exe' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
    if (-not $tool) { $tool = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe' }
    if (-not (Test-Path -LiteralPath $tool)) { throw 'MpCmdRun.exe не установлен. Проверьте Windows Security.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $tool
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft') {
        throw 'Подпись MpCmdRun.exe не подтверждена. Средство не будет запущено.'
    }
    $started = Get-Date
    $scanOutput = & $tool '-Scan' '-ScanType' ([string]$scanType) 2>&1
    $code = $LASTEXITCODE
    $result.exit_code = $code
    $result.output = ($scanOutput -join [Environment]::NewLine)
    $status = Get-MpComputerStatus
    $begin = $status.QuickScanStartTime; $end = $status.QuickScanEndTime
    if ($scanType -eq 2) { $begin=$status.FullScanStartTime; $end=$status.FullScanEndTime }
    $result.completed = ($code -in @(0,2) -and $begin -and $end -and $begin -ge $started.AddSeconds(-2) -and $end -ge $begin)
    if (-not $result.completed) { $warnings.Add('Завершение запрошенной проверки не подтверждено: сверяйте журнал Windows Security.') }
    $result.defender = Read-Defender
}

try {
    switch ($Mode) {
        'audit' { Audit-Computer }
        'files' { Inspect-Files }
        default { Scan-Defender }
    }
    $result.warnings=@($warnings.ToArray())
    [Console]::WriteLine((ConvertTo-Json -InputObject $result -Depth 10 -Compress))
    exit 0
} catch {
    [Console]::Error.WriteLine($_.ToString())
    exit 1
}
