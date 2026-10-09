# Read-only collector test using explicit fixtures, not a real Windows scan.
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$root=Split-Path -Parent $PSScriptRoot
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'security_probe.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
foreach ($function in $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
    Invoke-Expression $function.Extent.Text
}
function Get-CimInstance {
    param($ClassName,$Namespace)
    switch ($ClassName) {
        'Win32_Process' { [pscustomobject]@{ProcessId=999999;Name='fixture.exe';ExecutablePath='C:\fixture.exe';CommandLine='fixture.exe'} }
        'Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine' { [pscustomobject]@{Name='pid_999999_engtype_Compute';UtilizationPercentage=75} }
        'CommandLineEventConsumer' { [pscustomobject]@{Name='fixture-consumer';CommandLineTemplate='fixture.exe';ScriptText='';ScriptFileName=''} }
    }
}
function Start-Sleep { param($Seconds) }
function Get-NetTCPConnection { param($State) [pscustomobject]@{OwningProcess=999999;RemoteAddress='192.0.2.1';RemotePort=3333} }
function Get-DnsClientCache { [pscustomobject]@{Entry='pool.supportxmr.com';Data='192.0.2.1'} }
function Get-ScheduledTask { [pscustomobject]@{TaskPath='\Fixture\';TaskName='Periodic';Settings=[pscustomobject]@{Enabled=$false};Actions=@([pscustomobject]@{Execute='fixture.exe';Arguments='-o stratum+tcp://pool.supportxmr.com:3333'})} }
function Get-MpComputerStatus { [pscustomobject]@{AntivirusEnabled=$true;RealTimeProtectionEnabled=$true;AMRunningMode='Normal';AntivirusSignatureAge=0;AntivirusSignatureVersion='fixture';QuickScanStartTime=$null;QuickScanEndTime=$null;FullScanStartTime=$null;FullScanEndTime=$null} }
function Get-MpThreat { [pscustomobject]@{ThreatName='PUA:Win32/CoinMiner';ThreatID=1;IsActive=$false;DidThreatExecute=$false;Resources=@('file:C:\fixture.exe')} }
function Get-MpPreference { [pscustomobject]@{ExclusionPath=@('C:\Fixture');ExclusionProcess=@('fixture.exe')} }
$warnings=New-Object 'System.Collections.Generic.List[string]'
$result=[ordered]@{mode='audit'}
Audit-Computer
if ($result.processes.Count -ne 1 -or $result.connections.Count -ne 1 -or $result.gpu.Count -ne 1) { throw 'Collector discarded process/network/GPU fixtures' }
if ($result.defender.threats[0].active) { throw 'Defender history was marked active' }
if ($result.defender.exclusions_paths[0] -ne 'C:\Fixture') { throw 'Defender exclusion not preserved' }
if (@($result.persistence | Where-Object { $_.source -eq 'Все задания планировщика' -and $_.enabled -eq $false }).Count -ne 1) { throw 'Disabled periodic task was not preserved' }
if (@($result.persistence | Where-Object { $_.source -eq 'WMI CommandLineEventConsumer' }).Count -ne 1) { throw 'WMI consumer not preserved' }
Write-Output 'Security PASS: collector fixtures, process/GPU/TCP/DNS, all task actions, WMI, inactive Defender history and exclusions.'
