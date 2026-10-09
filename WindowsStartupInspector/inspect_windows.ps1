[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidateSet('startup','services')][string]$Section)

# Windows PowerShell 5.1. This file is UTF-8 WITH BOM for Russian text on Windows.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$script:Items = New-Object 'System.Collections.Generic.List[object]'
$script:Warnings = New-Object 'System.Collections.Generic.List[string]'
$script:Ids = @{}
$script:FileDescriptions = @{}
$nativeView = [Microsoft.Win32.RegistryView]::Registry32
if ([Environment]::Is64BitOperatingSystem) { $nativeView = [Microsoft.Win32.RegistryView]::Registry64 }

function Warn([string]$Where, [object]$Failure) {
    $script:Warnings.Add(('{0}: {1}' -f $Where, $Failure.ToString()))
}

function Add-Item([hashtable]$Item) {
    if (-not $script:Ids.ContainsKey($Item.id)) {
        $script:Items.Add([pscustomobject]$Item)
        $script:Ids[$Item.id] = $true
    }
}

function File-Description([string]$Command) {
    if ([string]::IsNullOrWhiteSpace($Command)) { return '' }
    $expanded = [Environment]::ExpandEnvironmentVariables($Command).Trim()
    if ($expanded.StartsWith('\SystemRoot', [StringComparison]::OrdinalIgnoreCase)) {
        $expanded = $env:SystemRoot + $expanded.Substring(11)
    }
    $path = ''
    if ($expanded -match '^"([^"]+)"') { $path = $Matches[1] }
    elseif ($expanded -match '^(.+?\.(?:exe|com|sys|dll))(?=\s|$)') { $path = $Matches[1] }
    elseif ($expanded -match '^(\S+)') { $path = $Matches[1] }
    if ($script:FileDescriptions.ContainsKey($path)) { return $script:FileDescriptions[$path] }
    $description = ''
    try {
        if ([IO.File]::Exists($path)) {
            $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($path)
            $description = [string]$version.FileDescription
            if ($version.CompanyName) { $description += ' [' + $version.CompanyName + ']' }
        }
    } catch { }
    $script:FileDescriptions[$path] = $description
    return $description
}

function User-Name([string]$Sid) {
    try {
        $identity = New-Object Security.Principal.SecurityIdentifier($Sid)
        return $identity.Translate([Security.Principal.NTAccount]).Value
    } catch { return $Sid }
}

function Approval-State($Base, [string]$Path, [string]$Name) {
    $key = $null
    try {
        $key = $Base.OpenSubKey($Path)
        if (-not $key) { return 'Включено (нет отметки отключения)' }
        $value = $key.GetValue($Name)
        if ($null -eq $value -or $value.Length -eq 0) { return 'Включено (нет отметки отключения)' }
        switch ([int]$value[0]) {
            2 { return 'Включено' }
            6 { return 'Включено' }
            3 { return 'Отключено' }
            7 { return 'Отключено' }
            default { return ('Неизвестная отметка ({0})' -f $value[0]) }
        }
    } catch {
        Warn ('StartupApproved ' + $Path + '\' + $Name) $_
        return 'Не удалось прочитать состояние'
    } finally { if ($key) { $key.Close() } }
}

function Read-RunKeys($Hive, [string]$Prefix, [string]$Owner, [string]$HiveLabel, $View) {
    $base = $null
    try {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($Hive, $View)
        $paths = @('Software\Microsoft\Windows\CurrentVersion\Run',
                   'Software\Microsoft\Windows\CurrentVersion\RunOnce',
                   'Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run')
        foreach ($relative in $paths) {
            $key = $null
            $full = $Prefix + $relative
            try {
                $key = $base.OpenSubKey($full)
                if (-not $key) { continue }
                foreach ($name in $key.GetValueNames()) {
                    try {
                        $command = [string]$key.GetValue($name, '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                        $state = 'Настроено'
                        if ($relative -eq $paths[0]) {
                            $approval = 'Run'
                            if ([Environment]::Is64BitOperatingSystem -and $View -eq [Microsoft.Win32.RegistryView]::Registry32) { $approval = 'Run32' }
                            # Explorer stores both approval lists in the native registry view.
                            $approvalBase = [Microsoft.Win32.RegistryKey]::OpenBaseKey($Hive, $nativeView)
                            try { $state = Approval-State $approvalBase ($Prefix + 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\' + $approval) $name }
                            finally { $approvalBase.Close() }
                        }
                        $display = $name
                        if (-not $display) { $display = '(По умолчанию)' }
                        Add-Item @{
                            id = ('registry|{0}|{1}|{2}|{3}' -f $HiveLabel, $View, $full, $name)
                            name = $display; kind = 'startup'; source = ('Реестр ({0})' -f $View)
                            owner = $Owner; command = $command; state = $state; start_mode = 'При входе'
                            description = (File-Description $command)
                            details = ('Ключ: {0}\{1}' -f $HiveLabel, $full) + "`n" +
                                      'RunOnce выполняется однократно; фактический запуск зависит от правил Windows.'
                        }
                    } catch { Warn ('Значение ' + $HiveLabel + '\' + $full + '\' + $name) $_ }
                }
            } catch { Warn ('Реестр ' + $HiveLabel + '\' + $full + ' / ' + $View) $_ }
            finally { if ($key) { $key.Close() } }
        }
    } catch { Warn ('Реестр ' + $HiveLabel + ' / ' + $View) $_ }
    finally { if ($base) { $base.Close() } }
}

function Read-StartupFolders($Users, $Machine, [string[]]$LoadedSids) {
    $folders = @{}
    $common = [Environment]::GetFolderPath([Environment+SpecialFolder]::CommonStartup)
    if ($common) { $folders[$common] = @{ owner = 'Все пользователи'; sid = '' } }
    $current = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($current) { $folders[$current] = @{ owner = (User-Name $currentSid); sid = $currentSid } }
    $profiles = $null
    try {
        $profiles = $Machine.OpenSubKey('SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList')
        if ($profiles) {
            foreach ($sid in $profiles.GetSubKeyNames()) {
                $profile = $null
                try {
                    $profile = $profiles.OpenSubKey($sid)
                    $path = [Environment]::ExpandEnvironmentVariables([string]$profile.GetValue('ProfileImagePath'))
                    if ($path) {
                        $folder = Join-Path $path 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'
                        $folders[$folder] = @{ owner = (User-Name $sid); sid = $sid }
                    }
                } catch { Warn ('Папка Startup профиля ' + $sid) $_ }
                finally { if ($profile) { $profile.Close() } }
            }
        }
        foreach ($sid in $LoadedSids) {
            $key = $null
            try {
                $key = $Users.OpenSubKey($sid + '\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders')
                if ($key) {
                    $redirected = [string]$key.GetValue('Startup', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                    # Do not expand another user's USERPROFILE to the current user's path.
                    $profileKey = $Machine.OpenSubKey('SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\' + $sid)
                    try {
                        if ($profileKey) {
                            $profilePath = [Environment]::ExpandEnvironmentVariables([string]$profileKey.GetValue('ProfileImagePath'))
                            $redirected = $redirected -replace '(?i)%USERPROFILE%', $profilePath
                            $redirected = $redirected -replace '(?i)%APPDATA%', ($profilePath + '\AppData\Roaming')
                            $redirected = [Environment]::ExpandEnvironmentVariables($redirected)
                            if ($redirected) { $folders[$redirected] = @{ owner = (User-Name $sid); sid = $sid } }
                        }
                    } finally { if ($profileKey) { $profileKey.Close() } }
                }
            } catch { Warn ('Путь Startup пользователя ' + $sid) $_ }
            finally { if ($key) { $key.Close() } }
        }
    } catch { Warn 'Профили пользователей / Startup' $_ }
    finally { if ($profiles) { $profiles.Close() } }
    $shell = $null
    try { $shell = New-Object -ComObject WScript.Shell } catch { Warn 'Чтение ярлыков Startup' $_ }
    try {
        foreach ($folder in ($folders.Keys | Sort-Object)) {
            try {
                # Get-Item reports access denial; Directory.Exists would silently return false.
                $directory = Get-Item -LiteralPath $folder -Force -ErrorAction Stop
                if (-not $directory.PSIsContainer) { continue }
                $files = @(Get-ChildItem -LiteralPath $folder -File -Force -ErrorAction Stop)
                foreach ($file in $files) {
                    if ($file.Name -ieq 'desktop.ini') { continue }
                    $command = $file.FullName
                    $description = ''
                    $details = 'Файл: ' + $file.FullName
                    if ($file.Extension -ieq '.lnk' -and $shell) {
                        $shortcut = $shell.CreateShortcut($file.FullName)
                        $command = '"' + $shortcut.TargetPath + '" ' + $shortcut.Arguments
                        $description = [string]$shortcut.Description
                        $details += "`nРабочая папка: " + $shortcut.WorkingDirectory
                    }
                    if (-not $description) { $description = File-Description $command }
                    $info = $folders[$folder]
                    $approvalBase = $Machine
                    $prefix = ''
                    if ($info.sid) { $approvalBase = $Users; $prefix = $info.sid + '\' }
                    $state = Approval-State $approvalBase ($prefix + 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder') $file.Name
                    Add-Item @{
                        id = ('folder|' + $file.FullName.ToLowerInvariant()); name = $file.Name
                        kind = 'startup'; source = 'Папка Startup'; owner = $info.owner
                        command = $command; description = $description; state = $state
                        start_mode = 'При входе'; details = $details
                    }
                }
            } catch [System.Management.Automation.ItemNotFoundException] { }
            catch { Warn ('Папка Startup ' + $folder) $_ }
        }
    } finally { if ($shell) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell) } }
}

function Read-StartupTasks {
    try {
        $taskErrors = @()
        $tasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue -ErrorVariable taskErrors)
        foreach ($errorRecord in $taskErrors) { Warn 'Планировщик заданий' $errorRecord }
        foreach ($task in $tasks) {
            try {
                $triggers = @($task.Triggers | Where-Object { $_.CimClass.CimClassName -match '(Boot|Logon)Trigger$' })
                if ($triggers.Count -eq 0) { continue }
                $commands = @($task.Actions | ForEach-Object {
                    if ($_.Execute) { ('"{0}" {1}' -f $_.Execute, $_.Arguments).Trim() }
                    elseif ($_.ClassId) { 'COM handler: ' + $_.ClassId }
                    else { $_.CimClass.CimClassName }
                })
                $types = @($triggers | ForEach-Object {
                    $label = 'При входе'
                    if ($_.CimClass.CimClassName -match 'BootTrigger$') { $label = 'При загрузке' }
                    if ($_.Enabled -eq $false) { $label += ' (триггер отключён)' }
                    $label
                })
                $state = 'Включено'
                if ($task.State -eq 'Disabled' -or $task.Settings.Enabled -eq $false) { $state = 'Отключено' }
                elseif (@($triggers | Where-Object { $_.Enabled -ne $false }).Count -eq 0) { $state = 'Отключено' }
                $command = $commands -join "`n"
                $description = [string]$task.Description
                if (-not $description) { $description = File-Description $command }
                Add-Item @{
                    id = ('task|' + $task.TaskPath + $task.TaskName); name = $task.TaskName
                    kind = 'startup'; source = 'Планировщик: ' + $task.TaskPath; owner = [string]$task.Principal.UserId
                    command = $command; description = $description; state = $state
                    start_mode = ($types -join ', ')
                    details = ('Задание: {0}{1}' -f $task.TaskPath, $task.TaskName) + "`n" +
                        ('Триггеры: {0}. Состояние планировщика: {1}. Уровень: {2}.' -f ($types -join ', '), $task.State, $task.Principal.RunLevel) + "`n" +
                        'Настроенный триггер не гарантирует запуск: действуют условия питания, сети, задержки и пользователя.'
                }
            } catch { Warn ('Задание ' + $task.TaskPath + $task.TaskName) $_ }
        }
    } catch { Warn 'Планировщик заданий' $_ }
}

function Read-LogonValues($Base, [string]$Prefix, [string]$Owner, [string]$HiveLabel) {
    $path = $Prefix + 'Software\Microsoft\Windows NT\CurrentVersion\Winlogon'
    $key = $null
    try {
        $key = $Base.OpenSubKey($path)
        if ($key) {
            foreach ($name in @('Shell','Userinit')) {
                $command = [string]$key.GetValue($name)
                if (-not $command) { continue }
                Add-Item @{
                    id = ('winlogon|' + $HiveLabel + '|' + $path + '|' + $name); name = 'Winlogon: ' + $name
                    kind = 'startup'; source = 'Winlogon'; owner = $Owner; command = $command
                    description = (File-Description $command); state = 'Настроено'; start_mode = 'При входе'
                    details = ('Ключ: {0}\{1}\{2}' -f $HiveLabel, $path, $name) + "`nПараметр системного входа Windows."
                }
            }
        }
    } catch { Warn ('Winlogon ' + $HiveLabel + '\' + $path) $_ }
    finally { if ($key) { $key.Close() } }
}

function Read-Startup {
    $views = @([Microsoft.Win32.RegistryView]::Registry32)
    if ([Environment]::Is64BitOperatingSystem) { $views += [Microsoft.Win32.RegistryView]::Registry64 }
    $users = $null
    $machine = $null
    try {
        $users = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::Users, $nativeView)
        $machine = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $nativeView)
        $sids = @($users.GetSubKeyNames() | Where-Object { $_ -match '^S-1-' -and $_ -notmatch '_Classes$' })
        foreach ($view in $views) {
            Read-RunKeys ([Microsoft.Win32.RegistryHive]::LocalMachine) '' 'Все пользователи' 'HKLM' $view
            foreach ($sid in $sids) {
                Read-RunKeys ([Microsoft.Win32.RegistryHive]::Users) ($sid + '\') (User-Name $sid) 'HKU' $view
            }
        }
        Read-StartupFolders $users $machine $sids
        Read-LogonValues $machine '' 'Все пользователи' 'HKLM'
        foreach ($sid in $sids) { Read-LogonValues $users ($sid + '\') (User-Name $sid) 'HKU' }
        $session = $null
        try {
            $session = $machine.OpenSubKey('SYSTEM\CurrentControlSet\Control\Session Manager')
            if ($session) {
                $commands = @($session.GetValue('BootExecute'))
                if ($commands.Count -gt 0 -and $commands[0]) {
                    Add-Item @{
                        id = 'bootexecute'; name = 'BootExecute'; kind = 'startup'; source = 'Session Manager'
                        owner = 'Система'; command = ($commands -join "`n"); description = 'Ранние системные команды загрузки Windows'
                        state = 'Настроено'; start_mode = 'При загрузке'
                        details = 'HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\BootExecute'
                    }
                }
            }
        } catch { Warn 'BootExecute' $_ }
        finally { if ($session) { $session.Close() } }
    } catch { Warn 'Источники реестра / Startup' $_ }
    finally {
        if ($users) { $users.Close() }
        if ($machine) { $machine.Close() }
    }
    Read-StartupTasks
}

function Read-Services {
    $states = @{ Running='Работает'; Stopped='Остановлена'; StartPending='Запускается'; StopPending='Останавливается';
                 Paused='Приостановлена'; PausePending='Приостанавливается'; ContinuePending='Возобновляется'; Unknown='Неизвестно' }
    $modes = @{ Auto='Автоматически'; Automatic='Автоматически'; Manual='Вручную'; Disabled='Отключена';
                Boot='Загрузка (Boot)'; System='Система (System)' }
    foreach ($class in @('Win32_Service','Win32_SystemDriver')) {
        try {
            $services = @(Get-CimInstance -ClassName $class -ErrorAction Stop)
            foreach ($service in $services) {
                $state = [string]$service.State
                if ($states.ContainsKey($state)) { $state = $states[$state] }
                $mode = [string]$service.StartMode
                if ($modes.ContainsKey($mode)) { $mode = $modes[$mode] }
                $kind = 'service'
                $source = 'Служба Windows / Win32_Service'
                if ($class -eq 'Win32_SystemDriver') { $kind = 'driver'; $source = 'Драйвер / Win32_SystemDriver' }
                $key = $null
                $base = $null
                $extra = @()
                try {
                    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $nativeView)
                    $key = $base.OpenSubKey('SYSTEM\CurrentControlSet\Services\' + $service.Name)
                    if ($key) {
                        if ($mode -eq 'Автоматически' -and $key.GetValue('DelayedAutoStart', 0) -eq 1) { $mode += ' (отложенно)' }
                        $deps = @($key.GetValue('DependOnService')) + @($key.GetValue('DependOnGroup'))
                        $deps = @($deps | Where-Object { $_ })
                        if ($deps.Count -gt 0) { $extra += 'Зависимости: ' + ($deps -join ', ') }
                        $triggerKey = $key.OpenSubKey('TriggerInfo')
                        try {
                            if ($triggerKey) { $extra += 'Триггеров запуска/остановки: ' + $triggerKey.SubKeyCount }
                        } finally { if ($triggerKey) { $triggerKey.Close() } }
                    }
                } catch { Warn ('Параметры службы ' + $service.Name) $_; $extra += 'Часть параметров службы недоступна.' }
                finally { if ($key) { $key.Close() }; if ($base) { $base.Close() } }
                $description = [string]$service.Description
                if (-not $description) { $description = File-Description ([string]$service.PathName) }
                if (-not $description) { $description = 'Описание не предоставлено Windows или файлом.' }
                $display = [string]$service.DisplayName
                if (-not $display) { $display = [string]$service.Name }
                Add-Item @{
                    id = ('service|' + $service.Name.ToLowerInvariant()); name = $display + ' [' + $service.Name + ']'
                    kind = $kind; source = $source; owner = [string]$service.StartName
                    command = [string]$service.PathName; description = $description; state = $state; start_mode = $mode
                    details = ('Системное имя: {0}. Тип: {1}. Код выхода: {2}.' -f $service.Name, $service.ServiceType, $service.ExitCode) + "`n" +
                        ($extra -join "`n") + "`nКлюч: HKLM\SYSTEM\CurrentControlSet\Services\" + $service.Name
                }
            }
        } catch { Warn ('Перечисление ' + $class) $_ }
    }
}

try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $administrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($Section -eq 'startup') { Read-Startup } else { Read-Services }
    $result = [ordered]@{
        section = $Section; collected_at = [DateTimeOffset]::Now.ToString('yyyy-MM-dd HH:mm:ss zzz')
        administrator = $administrator; items = @($script:Items.ToArray()); warnings = @($script:Warnings.ToArray())
    }
    $json = ConvertTo-Json -InputObject $result -Depth 8 -Compress
    [Console]::WriteLine($json)
    exit 0
} catch {
    [Console]::Error.WriteLine($_.ToString())
    exit 1
}
