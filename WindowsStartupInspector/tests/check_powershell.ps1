# Syntax and collector logic checks. Real Windows integration remains a separate check.
$ErrorActionPreference = 'Stop'
$folder = Split-Path -Parent $PSScriptRoot
foreach ($filename in @('inspect_windows.ps1', 'manage_windows.ps1', 'security_probe.ps1', 'Build_StartupInspector.ps1', 'tests/check_powershell.ps1', 'tests/check_controls.ps1', 'tests/check_security.ps1')) {
    $tokens = $null
    $parseErrors = $null
    $tree = [Management.Automation.Language.Parser]::ParseFile((Join-Path $folder $filename), [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
}
$tokens = $null
$parseErrors = $null
$tree = [Management.Automation.Language.Parser]::ParseFile((Join-Path $folder 'inspect_windows.ps1'), [ref]$tokens, [ref]$parseErrors)
$definitions = $tree.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false)
foreach ($definition in $definitions) { Invoke-Expression $definition.Extent.Text }
$script:Items = New-Object 'System.Collections.Generic.List[object]'
$script:Warnings = New-Object 'System.Collections.Generic.List[string]'
$script:Ids = @{}
$script:FileDescriptions = @{}
$nativeView = [Microsoft.Win32.RegistryView]::Registry64

function Assert-Equal($Actual, $Expected, [string]$Context) {
    if ($Actual -ne $Expected) { throw ('{0}: expected {1}, received {2}' -f $Context, $Expected, $Actual) }
}

$script:ApprovalValue = $null
$fakeKey = New-Object psobject
$fakeKey | Add-Member ScriptMethod GetValue { param($name) return $script:ApprovalValue }
$fakeKey | Add-Member ScriptMethod Close { }
$fakeBase = New-Object psobject
$fakeBase | Add-Member ScriptMethod OpenSubKey { param($path) return $fakeKey }
foreach ($code in @(2,3,6,7,255)) {
    $script:ApprovalValue = [byte[]]@($code,0,0,0)
    $expected = 'Включено'
    if ($code -in @(3,7)) { $expected = 'Отключено' }
    if ($code -eq 255) { $expected = 'Неизвестная отметка (255)' }
    Assert-Equal (Approval-State $fakeBase 'fake-path' 'fake-name') $expected ('approval code ' + $code)
}
$script:ApprovalValue = $null
Assert-Equal (Approval-State $fakeBase 'fake-path' 'fake-name') 'Включено (нет отметки отключения)' 'missing approval'
$deniedBase = New-Object psobject
$deniedBase | Add-Member ScriptMethod OpenSubKey { throw 'Access denied for test' }
Assert-Equal (Approval-State $deniedBase 'fake-path' 'fake-name') 'Не удалось прочитать состояние' 'denied approval'
Assert-Equal $script:Warnings.Count 1 'approval warning retained'

# The CIM adapter returns fixtures; registry access is unavailable on Linux and must warn.
function Get-CimInstance {
    param([string]$ClassName, [string]$ErrorAction)
    if ($ClassName -eq 'Win32_Service') {
        [pscustomobject]@{ Name='StoppedFixture'; DisplayName='Stopped'; State='Stopped'; StartMode='Disabled';
            Description='Disabled fixture'; PathName='C:\fixture.exe'; StartName='LocalSystem'; ServiceType='Own Process'; ExitCode=0 }
        [pscustomobject]@{ Name='RunningFixture'; DisplayName='Running'; State='Running'; StartMode='Manual';
            Description='Manual fixture'; PathName='C:\manual.exe'; StartName='LocalService'; ServiceType='Own Process'; ExitCode=0 }
    } else {
        [pscustomobject]@{ Name='DriverFixture'; DisplayName='Driver'; State='Running'; StartMode='Boot';
            Description='Driver fixture'; PathName='C:\fixture.sys'; StartName=''; ServiceType='Kernel Driver'; ExitCode=0 }
    }
}
Read-Services
Assert-Equal $script:Items.Count 3 'all fixture services retained'
Assert-Equal @($script:Items | Where-Object { $_.kind -eq 'driver' }).Count 1 'driver included'
Assert-Equal @($script:Items | Where-Object { $_.start_mode -eq 'Отключена' }).Count 1 'disabled service included'
Assert-Equal @($script:Items | Where-Object { $_.state -eq 'Работает' }).Count 2 'running state mapping'
Write-Output 'PowerShell PASS: scripts parse, approval flags, missing/denied approval, disabled and manual services, drivers, state mapping.'
