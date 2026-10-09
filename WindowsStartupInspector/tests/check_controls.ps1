# Tests of filesystem transactions and literal task selection with Windows adapters mocked.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'manage_windows.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($function in $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
    Invoke-Expression $function.Extent.Text
}
function Assert-True($Value,[string]$Message) { if (-not $Value) { throw $Message } }
function Reset-FolderApproval($Target,[string]$name) { }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('inspector-test-' + [Guid]::NewGuid().ToString('N'))
$folder = Join-Path $temporary 'Startup'
[IO.Directory]::CreateDirectory($folder) | Out-Null
$target = @{folder=$folder;filename='test.lnk';sid=''}
$live = Join-Path $folder 'test.lnk'
$backup = Join-Path (Join-Path $temporary 'WindowsStartupInspector_Disabled_Startup') 'test.lnk'
try {
    [IO.File]::WriteAllText($live, 'test fixture')
    Toggle-Folder $target 'disable' | Out-Null
    Assert-True ((Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $live)) 'Disable did not move file'
    Toggle-Folder $target 'enable' | Out-Null
    Assert-True (([IO.File]::ReadAllText($live) -eq 'test fixture') -and -not (Test-Path -LiteralPath $backup)) 'Enable did not restore file'
    Toggle-Folder $target 'enable' | Out-Null
    Assert-True (Test-Path -LiteralPath $live) 'Enabling an existing Startup file moved it unexpectedly'
    [IO.File]::WriteAllText($backup, 'preexisting backup')
    $blocked = $false
    try { Toggle-Folder $target 'disable' | Out-Null } catch { $blocked=$true }
    Assert-True $blocked 'Existing backup was overwritten'
    Assert-True ([IO.File]::ReadAllText($backup) -eq 'preexisting backup') 'Backup data changed'
    Remove-Item -LiteralPath $backup
    Toggle-Folder $target 'disable' | Out-Null
    function Reset-FolderApproval($Target,[string]$name) { throw 'Simulated approval failure' }
    $blocked=$false
    try { Toggle-Folder $target 'enable' | Out-Null } catch { $blocked=$true }
    Assert-True ($blocked -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $live)) 'Enable failure did not roll back move'
} finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force } }

$script:task = [pscustomobject]@{TaskPath='\Example\';TaskName='Task[1]';Settings=[pscustomobject]@{Enabled=$true}}
$script:other = [pscustomobject]@{TaskPath='\Example\';TaskName='Task1';Settings=[pscustomobject]@{Enabled=$true}}
function Get-ScheduledTask { $script:task; $script:other }
function Disable-ScheduledTask {
    param([Parameter(ValueFromPipeline=$true)]$InputObject)
    process { $InputObject.Settings.Enabled=$false; $InputObject }
}
function Enable-ScheduledTask {
    param([Parameter(ValueFromPipeline=$true)]$InputObject)
    process { $InputObject.Settings.Enabled=$true; $InputObject }
}
$target = @{task_path='\Example\';task_name='Task[1]'}
Toggle-Task $target 'disable' | Out-Null
Assert-True (-not $script:task.Settings.Enabled -and $script:other.Settings.Enabled) 'Task names were treated as wildcard patterns'
Toggle-Task $target 'enable' | Out-Null
Assert-True $script:task.Settings.Enabled 'Task enable failed'
$blocked=$false
try { Toggle-Run @{hive='HKLM';view='Registry64';key='SYSTEM\CurrentControlSet\Services';value='test'} 'disable' | Out-Null } catch { $blocked=$true }
Assert-True $blocked 'Unexpected registry key was accepted'
Write-Output 'Controls PASS: file disable/restore, no overwrite, rollback, literal task name, registry scope.'
