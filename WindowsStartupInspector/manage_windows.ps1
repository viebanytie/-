[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RequestFile)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

function Reset-RunApproval($Target) {
    if ($Target.key -notmatch '(?i)\\CurrentVersion\\Run$') { return }
    $prefix = ''
    if ($Target.hive -eq 'HKU') { $prefix = ($Target.key -split '\\')[0] + '\' }
    $view = [Microsoft.Win32.RegistryView]::Registry32
    if ([Environment]::Is64BitOperatingSystem) { $view = [Microsoft.Win32.RegistryView]::Registry64 }
    $leaf = 'Run'
    if ([Environment]::Is64BitOperatingSystem -and $Target.view -eq 'Registry32') { $leaf = 'Run32' }
    $hive = [Microsoft.Win32.RegistryHive]::LocalMachine
    if ($Target.hive -eq 'HKU') { $hive = [Microsoft.Win32.RegistryHive]::Users }
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
    $key = $null
    try {
        $key = $base.CreateSubKey($prefix + 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\' + $leaf)
        $value = New-Object byte[] 12
        $value[0] = 2
        $key.SetValue([string]$Target.value, $value, [Microsoft.Win32.RegistryValueKind]::Binary)
    } finally { if ($key) { $key.Close() }; $base.Close() }
}

function Toggle-Run($Target, [string]$Action) {
    if ($Target.hive -notin @('HKLM','HKU') -or $Target.view -notin @('Registry32','Registry64')) { throw 'Некорректный адрес реестра.' }
    $prefix = ''
    if ($Target.hive -eq 'HKU') { $prefix = 'S-1-[0-9-]+\\' }
    $pattern = '(?i)^' + $prefix + 'Software\\Microsoft\\Windows\\CurrentVersion\\(?:Run|RunOnce|Policies\\Explorer\\Run)$'
    if ($Target.key -notmatch $pattern) { throw 'Изменение этого ключа реестра не поддерживается.' }
    $hive = [Microsoft.Win32.RegistryHive]::LocalMachine
    if ($Target.hive -eq 'HKU') { $hive = [Microsoft.Win32.RegistryHive]::Users }
    $view = [Enum]::Parse([Microsoft.Win32.RegistryView], [string]$Target.view)
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
    $key = $null; $backup = $null
    try {
        $key = $base.OpenSubKey([string]$Target.key, $true)
        if (-not $key) { throw 'Ключ автозапуска больше не существует. Обновите список.' }
        $backup = $key.CreateSubKey('WindowsStartupInspector_Disabled')
        $name = [string]$Target.value
        $liveExists = $key.GetValueNames() -contains $name
        $backupExists = $backup.GetValueNames() -contains $name
        if ($Action -eq 'disable') {
            if (-not $liveExists -and $backupExists) { return 'Запись уже отключена.' }
            if (-not $liveExists) { throw 'Значение автозапуска больше не существует.' }
            if ($backupExists) { throw 'Резервная копия уже существует. Она не будет перезаписана.' }
            $kind = $key.GetValueKind($name)
            $data = $key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $backup.SetValue($name, $data, $kind)
            $check = $backup.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            if ((ConvertTo-Json -InputObject $check -Compress) -ne (ConvertTo-Json -InputObject $data -Compress) -or $backup.GetValueKind($name) -ne $kind) {
                $backup.DeleteValue($name)
                throw 'Резервная копия не совпала с исходным значением; отключение отменено.'
            }
            try { $key.DeleteValue($name, $true) }
            catch { $backup.DeleteValue($name, $false); throw }
            if ($key.GetValueNames() -contains $name) { throw 'Windows не подтвердила удаление значения из автозапуска.' }
            return 'Автозапуск отключён. Исходное значение сохранено в подветке WindowsStartupInspector_Disabled.'
        }
        if ($liveExists -and $backupExists) { throw 'Есть действующее значение и резервная копия. Конфликт нужно разобрать вручную: ничего не перезаписано.' }
        if (-not $liveExists -and -not $backupExists) { throw 'Значение и резервная копия отсутствуют.' }
        Reset-RunApproval $Target
        if ($backupExists) {
            $kind = $backup.GetValueKind($name)
            $data = $backup.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $key.SetValue($name, $data, $kind)
            $check = $key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            if ((ConvertTo-Json -InputObject $check -Compress) -ne (ConvertTo-Json -InputObject $data -Compress)) { throw 'Windows не подтвердила восстановление значения.' }
            $backup.DeleteValue($name)
        }
        return 'Автозапуск включён; отметка StartupApproved сброшена в состояние «Включено».'
    } finally { if ($backup) { $backup.Close() }; if ($key) { $key.Close() }; $base.Close() }
}

function Reset-FolderApproval($Target, [string]$name) {
    $hive = [Microsoft.Win32.RegistryHive]::LocalMachine
    $prefix = ''
        if ($Target.sid) { $hive = [Microsoft.Win32.RegistryHive]::Users; $prefix = $Target.sid + '\' }
    $view = [Microsoft.Win32.RegistryView]::Registry32
        if ([Environment]::Is64BitOperatingSystem) { $view = [Microsoft.Win32.RegistryView]::Registry64 }
    $base = $null; $key = $null
    try {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
        $key = $base.CreateSubKey($prefix + 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder')
        $flag = New-Object byte[] 12; $flag[0] = 2
        $key.SetValue($name, $flag, [Microsoft.Win32.RegistryValueKind]::Binary)
    } finally { if ($key) { $key.Close() }; if ($base) { $base.Close() } }
}

function Toggle-Folder($Target, [string]$Action) {
    $folder = [IO.Path]::GetFullPath([string]$Target.folder).TrimEnd('\')
    $name = [string]$Target.filename
    if (-not $name -or [IO.Path]::GetFileName($name) -ne $name -or $name -in @('.','..')) { throw 'Некорректное имя файла Startup.' }
    $live = Join-Path $folder $name
    $backupFolder = Join-Path (Split-Path -Parent $folder) 'WindowsStartupInspector_Disabled_Startup'
    $saved = Join-Path $backupFolder $name
    if ($Action -eq 'enable' -and (Test-Path -LiteralPath $live) -and -not (Test-Path -LiteralPath $saved)) {
        Reset-FolderApproval $Target $name
        return 'Файл уже находится в Startup. Его отметка StartupApproved включена.'
    }
    if ($Action -eq 'disable' -and -not (Test-Path -LiteralPath $live) -and (Test-Path -LiteralPath $saved)) { return 'Файл уже отключён и сохранён вне Startup.' }
    $from = $live; $to = $saved
    if ($Action -eq 'enable') { $from = $saved; $to = $live }
    if (Test-Path -LiteralPath $to) { throw 'Файл назначения уже существует и не будет перезаписан.' }
    $file = Get-Item -LiteralPath $from -Force
    if ($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Перемещение каталогов и ссылок файловой системы не поддерживается.' }
    [IO.Directory]::CreateDirectory((Split-Path -Parent $to)) | Out-Null
    [IO.File]::Move($from, $to)
    if (-not (Test-Path -LiteralPath $to) -or (Test-Path -LiteralPath $from)) { throw 'Windows не подтвердила перемещение файла.' }
    if ($Action -eq 'enable') {
        try { Reset-FolderApproval $Target $name }
        catch { [IO.File]::Move($live, $saved); throw }
        return 'Файл возвращён в Startup и включён. Уже работающая программа не перезапускается.'
    }
    return 'Файл перенесён из Startup в соседнюю папку WindowsStartupInspector_Disabled_Startup. Его можно включить обратно.'
}

function Read-OneTask($Target) {
    $matches = @(Get-ScheduledTask | Where-Object { $_.TaskPath -eq $Target.task_path -and $_.TaskName -eq $Target.task_name })
    if ($matches.Count -ne 1) { throw 'Выбранное задание не найдено однозначно. Обновите список.' }
    return $matches[0]
}

function Toggle-Task($Target, [string]$Action) {
    $task = Read-OneTask $Target
    if ($Action -eq 'disable') { $task | Disable-ScheduledTask | Out-Null }
    else { $task | Enable-ScheduledTask | Out-Null }
    $current = Read-OneTask $Target
    if (($Action -eq 'disable') -ne ($current.Settings.Enabled -eq $false)) { throw 'Планировщик не подтвердил новое состояние задания.' }
    return 'Состояние задания изменено. Триггеры, условия и уже работающие действия задания не изменяются.'
}

function Run-SC([string[]]$Arguments) {
    $output = & (Join-Path $env:SystemRoot 'System32\sc.exe') @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw ('sc.exe: ' + ($output -join [Environment]::NewLine)) }
}

function Manage-Service($Target, [string]$Action) {
    $name = [string]$Target.name
    if (-not $name -or $name -match '[\\/\x00-\x1f]') { throw 'Некорректное имя службы.' }
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Default)
    $key = $null
    try {
        $key = $base.OpenSubKey('SYSTEM\CurrentControlSet\Services\' + $name)
        if (-not $key) { throw 'Служба больше не существует. Обновите список.' }
        $start = [int]$key.GetValue('Start', -1)
        if ($start -notin @(0,1,2,3,4)) { throw 'Неизвестный тип запуска службы.' }
        $delayed = [int]$key.GetValue('DelayedAutoStart', 0)
    } finally { if ($key) { $key.Close() }; $base.Close() }
    if ($Action -in @('start','stop')) {
        Run-SC @($Action, $name)
        return 'Windows приняла команду. Фактическое состояние смотрите в обновлённом списке.'
    }
    if ($Action -eq 'enable' -and $start -ne 4) { return 'Служба уже имеет разрешённый тип запуска. Тип не изменён.' }
    $folder = Join-Path $env:LOCALAPPDATA 'WindowsStartupInspector\service-backups'
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $id = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($name.ToLowerInvariant()))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
    $backupPath = Join-Path $folder ($id + '.json')
    if ($Action -eq 'disable') {
        if ($start -eq 4) { return 'Тип запуска уже «Отключена».' }
        if (Test-Path -LiteralPath $backupPath) {
            $existing = ConvertFrom-Json ([IO.File]::ReadAllText($backupPath, [Text.Encoding]::UTF8))
            if ($existing.name -ne $name -or $existing.computer -ne $env:COMPUTERNAME -or [int]$existing.start -ne $start -or [int]$existing.delayed -ne $delayed) {
                throw 'Сохранённая копия отличается от текущих параметров. Отключение отменено; резервная копия не перезаписана.'
            }
        }
        [IO.Directory]::CreateDirectory($folder) | Out-Null
        if (-not (Test-Path -LiteralPath $backupPath)) {
            $backup = @{ name=$name; computer=$env:COMPUTERNAME; start=$start; delayed=$delayed; saved_at=[DateTimeOffset]::Now.ToString('o') }
            $temporary = $backupPath + '.tmp'
            [IO.File]::WriteAllText($temporary, (ConvertTo-Json -InputObject $backup), (New-Object Text.UTF8Encoding($false)))
            Move-Item -LiteralPath $temporary -Destination $backupPath
        }
        Run-SC @('config', $name, 'start=', 'disabled')
        $expected = 4
        $message = 'Тип запуска службы изменён на «Отключена». Работающая служба не остановлена; для этого есть отдельная команда.'
    } else {
        $restore = 3; $restoreDelayed = 0
        if (Test-Path -LiteralPath $backupPath) {
            $saved = ConvertFrom-Json ([IO.File]::ReadAllText($backupPath, [Text.Encoding]::UTF8))
            if ($saved.name -ne $name -or $saved.computer -ne $env:COMPUTERNAME -or [int]$saved.start -notin @(0,1,2,3)) { throw 'Резервная копия службы не соответствует компьютеру или содержит неверный тип.' }
            $restore = [int]$saved.start; $restoreDelayed = [int]$saved.delayed
        }
        $modes = @{0='boot';1='system';2='auto';3='demand'}
        $mode = $modes[$restore]
        if ($restore -eq 2 -and $restoreDelayed -eq 1) { $mode = 'delayed-auto' }
        Run-SC @('config', $name, 'start=', $mode)
        $expected = $restore
        $message = 'Тип запуска восстановлен из резервной копии. При отсутствии копии выбран ручной запуск. Для запуска сейчас используйте «Запустить».'
    }
    $checkBase = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Default)
    $checkKey = $null
    try {
        $checkKey = $checkBase.OpenSubKey('SYSTEM\CurrentControlSet\Services\' + $name)
        if (-not $checkKey -or [int]$checkKey.GetValue('Start', -1) -ne $expected) { throw 'Windows не подтвердила новый тип запуска службы.' }
    } finally { if ($checkKey) { $checkKey.Close() }; $checkBase.Close() }
    if ($Action -eq 'enable' -and (Test-Path -LiteralPath $backupPath)) { Remove-Item -LiteralPath $backupPath }
    return $message
}

try {
    $request = ConvertFrom-Json ([IO.File]::ReadAllText($RequestFile, [Text.Encoding]::UTF8))
    if ($request.action -notin @('disable','enable','start','stop')) { throw 'Неизвестное действие.' }
    if ($request.action -in @('start','stop') -and $request.target.type -ne 'service') { throw 'Запуск и остановка доступны только службам.' }
    $message = switch ($request.target.type) {
        'registry' { Toggle-Run $request.target $request.action }
        'folder' { Toggle-Folder $request.target $request.action }
        'task' { Toggle-Task $request.target $request.action }
        'service' { Manage-Service $request.target $request.action }
        default { throw 'Эта системная запись доступна только для просмотра.' }
    }
    [Console]::WriteLine((ConvertTo-Json -InputObject @{success=$true; id=$request.id; action=$request.action; message=$message} -Compress))
    exit 0
} catch {
    [Console]::Error.WriteLine($_.ToString() + [Environment]::NewLine + 'Для системных записей запустите программу от имени администратора. Защита Windows не обходится.')
    exit 1
}
