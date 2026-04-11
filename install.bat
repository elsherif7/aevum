@echo off
:: Self-elevate to admin if not already
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting admin rights...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

setlocal EnableDelayedExpansion

echo.
echo  ============================================================
echo    AEVUM Installer
echo  ============================================================
echo.

:: Get the folder this bat file is in (works even after elevation)
set "SRC=%~dp0"

:: Check that aevum.py is actually here before doing anything
if not exist "%SRC%aevum.py" (
    echo  [ERROR] aevum.py not found in %SRC%
    echo  Make sure install.bat and aevum.py are in the same folder.
    pause
    exit /b 1
)

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Download from https://python.org
    pause
    exit /b 1
)

:: Get Python Scripts path
for /f "delims=" %%A in ('python -c "import sysconfig; print(sysconfig.get_path(\"scripts\"))"') do set "SCRIPTS=%%A"

:: Make sure we actually got a scripts path
if "%SCRIPTS%"=="" (
    echo  [ERROR] Could not determine Python Scripts folder. Aborting.
    pause
    exit /b 1
)

set "INSTALL_DIR=%LOCALAPPDATA%\Aevum"

echo  App folder  : %INSTALL_DIR%
echo  Launcher in : %SCRIPTS%
echo.

:: Create app folder
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

:: Copy script and verify it actually landed
copy /Y "%SRC%aevum.py" "%INSTALL_DIR%\aevum.py" >nul
if not exist "%INSTALL_DIR%\aevum.py" (
    echo  [ERROR] Failed to copy aevum.py to %INSTALL_DIR%
    pause
    exit /b 1
)
echo  [OK] Copied aevum.py

:: Write launcher into Python Scripts
(
    echo @echo off
    echo python "%INSTALL_DIR%\aevum.py" %%*
) > "%SCRIPTS%\aevum.cmd"

:: Verify the launcher was actually created
if not exist "%SCRIPTS%\aevum.cmd" (
    echo  [ERROR] Failed to create aevum.cmd in %SCRIPTS%
    pause
    exit /b 1
)

echo  [OK] Created aevum.cmd in Python Scripts
echo.
echo  ============================================================
echo    SUCCESS! Open any cmd window and type:  aevum
echo  ============================================================
echo.
pause
