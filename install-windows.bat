@echo off
setlocal enabledelayedexpansion
title Wayne Enterprises // Batcomputer Installer (Windows)
color 0B

echo ================================================================
echo 🦇 WAYNE ENTERPRISES // BATCOMPUTER BCv1.2 INSTALLER (WINDOWS)
echo ================================================================
echo.

:: 1. Verify Python Installation
python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Python 3 was not found in your system PATH.
    echo Attempting to install Python via Windows Package Manager (winget)...
    winget install Python.Python.3.11 --silent --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo ❌ Automatic install failed. Please manually install Python 3.10+ from:
        echo    https://www.python.org/downloads/
        echo ⚠️  CRITICAL: Check the box "Add python.exe to PATH" during installation.
        echo.
        pause
        exit /b 1
    )
    echo ✓ Python installed successfully! Please restart this script to complete setup.
    pause
    exit /b 0
)

for /f "tokens=2 delims= " %%v in ('python --version') do set PY_VER=%%v
echo ✓ Python %PY_VER% verified.

:: 2. Setup Dedicated Virtual Environment
echo.
echo [1/3] Initializing Wayne Tech Virtual Environment (.venv)...
if not exist ".venv" (
    python -m venv .venv
    echo ✓ Virtual environment created in .venv\
) else (
    echo ✓ Existing virtual environment found.
)

set VENV_PY=.venv\Scripts\python.exe
set VENV_PIP=.venv\Scripts\pip.exe

:: 3. Upgrade Pip and Install Dependencies
echo.
echo [2/3] Installing dependencies (PyQt6, sounddevice, requests, etc.)...
%VENV_PIP% install --upgrade pip --quiet
%VENV_PIP% install -r requirements.txt --quiet
if errorlevel 1 (
    echo [!] Standard install encountered an issue; retrying dependencies individually...
    %VENV_PIP% install requests sounddevice psutil python-dateutil
    %VENV_PIP% install PyQt6 PyQt6-WebEngine
)

:: 4. Generate Launch Script & Desktop Shortcut
echo.
echo [3/3] Creating Launchers and Shortcuts...
set SCRIPT_DIR=%~dp0
set SHORTCUT_VBS=%TEMP%\CreateBatcomputerShortcut.vbs

echo Set oWS = WScript.CreateObject("WScript.Shell") > "%SHORTCUT_VBS%"
echo sLinkFile = oWS.SpecialFolders("Desktop") ^& "\Batcomputer Companion.lnk" >> "%SHORTCUT_VBS%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%SHORTCUT_VBS%"
echo oLink.TargetPath = "%SCRIPT_DIR%launch-alfred.bat" >> "%SHORTCUT_VBS%"
echo oLink.WorkingDirectory = "%SCRIPT_DIR%" >> "%SHORTCUT_VBS%"
echo oLink.Description = "Batcomputer BCv1.2 Voice Companion" >> "%SHORTCUT_VBS%"
echo oLink.Save >> "%SHORTCUT_VBS%"
cscript //nologo "%SHORTCUT_VBS%"
del "%SHORTCUT_VBS%" 2>nul
echo ✓ Desktop Shortcut created: "Batcomputer Companion"

:: 5. Optional Startup Configuration
echo.
set /p AUTO_START="⚡ Would you like the Batcomputer to launch automatically on Windows boot? (Y/N): "
if /i "!AUTO_START!"=="Y" (
    set STARTUP_VBS=%TEMP%\CreateStartupShortcut.vbs
    echo Set oWS = WScript.CreateObject("WScript.Shell") > "!STARTUP_VBS!"
    echo sLinkFile = oWS.SpecialFolders("Startup") ^& "\Batcomputer Companion.lnk" >> "!STARTUP_VBS!"
    echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "!STARTUP_VBS!"
    echo oLink.TargetPath = "%SCRIPT_DIR%launch-alfred.bat" >> "!STARTUP_VBS!"
    echo oLink.WorkingDirectory = "%SCRIPT_DIR%" >> "!STARTUP_VBS%"
    echo oLink.Description = "Batcomputer BCv1.2 Voice Companion" >> "!STARTUP_VBS!"
    echo oLink.Save >> "!STARTUP_VBS!"
    cscript //nologo "!STARTUP_VBS!"
    del "!STARTUP_VBS!" 2>nul
    echo ✓ Added to Windows Startup folder.
)

echo.
echo ================================================================
echo 🦇 INSTALLATION COMPLETE // WAYNE ENTERPRISES PROTOCOLS ACTIVE
echo ================================================================
echo Double-click the 'Batcomputer Companion' shortcut on your Desktop,
echo or run 'launch-alfred.bat' to start 'Puter.
echo ================================================================
echo.
pause