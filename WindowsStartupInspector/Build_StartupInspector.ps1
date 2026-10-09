[CmdletBinding()]
param([switch]$Clean)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Command failed ({0}): {1}' -f $LASTEXITCODE, $Program) }
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Build the Windows EXE on Windows 10/11, not on Linux.'
    }
    if (-not (Get-Command py.exe -ErrorAction SilentlyContinue)) {
        throw 'Install Python 3.12 x64 from python.org, including Tcl/Tk and the Python launcher.'
    }
    Checked 'py.exe' @('-3.12', '-c', "import struct, tkinter; assert struct.calcsize('P') == 8, 'Python x64 is required'")
    if (-not (Test-Path -LiteralPath '.venv-build\Scripts\python.exe')) {
        Checked 'py.exe' @('-3.12', '-m', 'venv', '.venv-build')
    }
    $python = Join-Path $PSScriptRoot '.venv-build\Scripts\python.exe'
    Checked $python @('-c', "import sys, struct, tkinter; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8, 'Recreate .venv-build with Python 3.12 x64'")
    Checked $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-r', 'requirements-build.txt')
    Checked $python @('-m', 'unittest', 'discover', '-s', 'tests', '-v')
    Checked 'powershell.exe' @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', 'tests\check_powershell.ps1')
    Checked 'powershell.exe' @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', 'tests\check_controls.ps1')
    Checked 'powershell.exe' @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', 'tests\check_security.ps1')
    # Keep generated spec/build files under ignored directories, never over source files.
    New-Item -ItemType Directory -Force -Path 'build' | Out-Null
    $collectorData = (Join-Path $PSScriptRoot 'inspect_windows.ps1') + ';.'
    $demoData = (Join-Path $PSScriptRoot 'demo.json') + ';.'
    $manageData = (Join-Path $PSScriptRoot 'manage_windows.ps1') + ';.'
    $securityData = (Join-Path $PSScriptRoot 'security_probe.ps1') + ';.'
    $buildArgs = @('-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
        '--name', 'WindowsStartupInspector', '--specpath', 'build', '--workpath', 'build\pyinstaller',
        '--distpath', 'dist', '--add-data', $collectorData, '--add-data', $demoData,
        '--add-data', $manageData, '--add-data', $securityData,
        (Join-Path $PSScriptRoot 'startup_inspector.py'))
    if ($Clean) { $buildArgs += '--clean' }
    Checked $python $buildArgs
    $exe = Join-Path $PSScriptRoot 'dist\WindowsStartupInspector.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw 'PyInstaller did not produce the expected EXE.' }
    Get-FileHash -Algorithm SHA256 -LiteralPath $exe | Format-List
    Write-Host 'Build completed. Run dist\WindowsStartupInspector.exe.'
    exit 0
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
