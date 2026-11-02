@echo off
setlocal enabledelayedexpansion
title 'Puter // BCv1.2 Batcomputer Companion
echo ================================================================
echo 🦇 'PUTER // BCv1.2 BATCOMPUTER VOICE COMPANION FOR WINDOWS
echo ================================================================
echo.

:: 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python was not found in PATH!
    echo Please install Python 3.10+ from https://python.org
    echo IMPORTANT: Make sure to check 'Add python.exe to PATH' during installation.
    echo.
    pause
    exit /b 1
)

:: 2. Auto-install dependencies if missing
echo [1/2] Verifying Python libraries (requests, sounddevice, PyQt6)...
python -c "import requests, sounddevice" >nul 2>&1
if errorlevel 1 (
    echo Installing required audio & network libraries...
    pip install requests sounddevice
)

python -c "import PyQt6" >nul 2>&1
if errorlevel 1 (
    echo Installing PyQt6 GUI framework...
    pip install PyQt6 PyQt6-WebEngine
)

:: 3. Launch 'Puter Daemon
echo [2/2] Connecting to Batcomputer backend...
echo Server: https://batcomputer.jmrdserver.duckdns.org
echo Wake words: "Computer", "Puter", "Hey Computer", "Hey Puter"
echo Follow-Up Window: 10s automatic conversation without wake word
echo.
python alfred_daemon.py --server "https://batcomputer.jmrdserver.duckdns.org" --hud-url "https://batcomputer.jmrdserver.duckdns.org"

pause
