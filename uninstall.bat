@echo off
:: Self-elevate to admin if not already
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting admin rights...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

setlocal

echo.
echo  ============================================================
echo    AEVUM Uninstaller
echo  ============================================================
echo.
echo  This will remove:
echo    - %%LOCALAPPDATA%%\Aevum  (the app folder)
echo    - aevum.cmd               (the launcher)
echo.
echo  Your PATH will NOT be touched.
echo.
set /p "CONFIRM=  Type YES to confirm: "
if /i not "%CONFIRM%"=="YES" (
    echo.
    echo  Cancelled. Nothing was removed.
    echo.
    pause
    exit /b 0
)

echo.
echo  Uninstalling Aevum...
echo.

set "INSTALL_DIR=%LOCALAPPDATA%\Aevum"

:: Remove app folder - only if it contains aevum.py (safety check)
if exist "%INSTALL_DIR%" (
    if not exist "%INSTALL_DIR%\aevum.py" (
        echo  [ERROR] %INSTALL_DIR% does not contain aevum.py. Aborting.
        echo  Nothing was removed.
        echo.
        pause
        exit /b 1
    )

    :: Warn if there are extra files that will also be deleted
    set "EXTRA=0"
    for /f "delims=" %%F in ('dir /b "%INSTALL_DIR%" 2^>nul ^| findstr /v /i "^aevum\.py$"') do set "EXTRA=1"
    if "!EXTRA!"=="1" (
        echo  [WARN] Extra files found in %INSTALL_DIR% - they will also be removed:
        dir /b "%INSTALL_DIR%" | findstr /v /i "^aevum\.py$"
        echo.
    )

    echo  Found: %INSTALL_DIR%
    rmdir /S /Q "%INSTALL_DIR%"
    if exist "%INSTALL_DIR%" (
        echo  [ERROR] Failed to remove %INSTALL_DIR%
    ) else (
        echo  [OK] Removed %INSTALL_DIR%
    )
) else (
    echo  [SKIP] App folder not found at %INSTALL_DIR%
)

:: Get Python Scripts path safely
python --version >nul 2>&1
if errorlevel 1 (
    echo  [SKIP] Python not found - cannot locate launcher. You may need to delete aevum.cmd manually.
    goto done
)

for /f "delims=" %%A in ('python -c "import sysconfig; print(sysconfig.get_path(\"scripts\"))"') do set "SCRIPTS=%%A"

:: Make sure we got a valid path before doing anything with it
if "%SCRIPTS%"=="" (
    echo  [SKIP] Could not determine Python Scripts folder. You may need to delete aevum.cmd manually.
    goto done
)

if exist "%SCRIPTS%\aevum.cmd" (
    del /Q "%SCRIPTS%\aevum.cmd"
    if exist "%SCRIPTS%\aevum.cmd" (
        echo  [ERROR] Failed to remove aevum.cmd from %SCRIPTS%
    ) else (
        echo  [OK] Removed aevum.cmd from %SCRIPTS%
    )
) else (
    echo  [SKIP] aevum.cmd not found in %SCRIPTS%
)

:done
echo.
echo  Aevum uninstalled.
echo.
pause
