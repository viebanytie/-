@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Build_StartupInspector.ps1" %*
set "BUILD_RESULT=%ERRORLEVEL%"
echo.
if not "%BUILD_RESULT%"=="0" echo Build failed. Read the error above.
if "%BUILD_RESULT%"=="0" echo EXE: dist\WindowsStartupInspector.exe
echo Press any key to close.
pause >nul
exit /b %BUILD_RESULT%
