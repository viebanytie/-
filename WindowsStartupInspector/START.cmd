@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\WindowsStartupInspector.exe" (
  start "" "%~dp0dist\WindowsStartupInspector.exe" %*
  exit /b 0
)
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.12 x64 from python.org with the Python launcher.
  pause
  exit /b 1
)
py -3.12 "%~dp0startup_inspector.py" %*
set "RUN_RESULT=%ERRORLEVEL%"
if not "%RUN_RESULT%"=="0" pause
exit /b %RUN_RESULT%
