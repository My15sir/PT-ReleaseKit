@echo off
setlocal EnableExtensions
set "SCRIPT_DIR=%~dp0"
set "APP_EXE=%SCRIPT_DIR%PT-BDtool.exe"
set "GUI_SCRIPT=%SCRIPT_DIR%ptbd-gui.py"

if exist "%APP_EXE%" (
  start "" "%APP_EXE%" %*
  exit /b 0
)

if not exist "%GUI_SCRIPT%" goto :missing_gui

where pyw >nul 2>nul
if not errorlevel 1 (
  start "" pyw -3 "%GUI_SCRIPT%" %*
  exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%GUI_SCRIPT%" %*
  exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%GUI_SCRIPT%" %*
  if %errorlevel%==0 exit /b 0
)

where python >nul 2>nul
if not errorlevel 1 (
  python "%GUI_SCRIPT%" %*
  if %errorlevel%==0 exit /b 0
)

echo [ERROR] Cannot find a usable Python launcher.
echo [HINT] Please install Python 3 first.
echo [HINT] Or use the packaged standalone PT-BDtool.exe instead.
pause
exit /b 1

:missing_gui
echo [ERROR] Cannot find ptbd-gui.py in this PT-BDtool folder.
echo [HINT] Please run this file from a complete PT-BDtool directory.
pause
exit /b 1
