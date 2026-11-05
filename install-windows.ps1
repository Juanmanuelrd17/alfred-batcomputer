<#
==============================================================================
WAYNE ENTERPRISES // BATCOMPUTER BCv1.2 POWERSHELL INSTALLER
Native Voice Companion & Tactical HUD Installer for Windows 10 & 11
==============================================================================
#>

$Host.UI.RawUI.WindowTitle = "Wayne Enterprises // Batcomputer Installer (Windows)"
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "🦇 WAYNE ENTERPRISES // BATCOMPUTER BCv1.2 POWERSHELL INSTALLER" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# 1. Verify Python
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
    Write-Host "⚠️ Python not found in system PATH. Attempting winget installation..." -ForegroundColor Yellow
    winget install Python.Python.3.11 --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Python installation failed. Please install Python 3.10+ from https://python.org" -ForegroundColor Red
        Write-Host "CRITICAL: Check 'Add python.exe to PATH' during installation." -ForegroundColor Yellow
        pause
        exit 1
    }
    Write-Host "✓ Python installed. Please re-run this script in a new PowerShell window." -ForegroundColor Green
    pause
    exit 0
}

$PyVer = & python --version 2>&1
Write-Host "✓ Detected $PyVer" -ForegroundColor Green

# 2. Virtual Environment
Write-Host ""
Write-Host "[1/3] Setting up Wayne Tech Virtual Environment (.venv)..." -ForegroundColor Cyan
if (-not (Test-Path "$ScriptDir\.venv")) {
    & python -m venv "$ScriptDir\.venv"
    Write-Host "✓ Created virtual environment in .venv\" -ForegroundColor Green
} else {
    Write-Host "✓ Existing virtual environment detected." -ForegroundColor Green
}

$VenvPython = "$ScriptDir\.venv\Scripts\python.exe"
$VenvPip = "$ScriptDir\.venv\Scripts\pip.exe"

# 3. Dependencies
Write-Host ""
Write-Host "[2/3] Installing Python dependencies (PyQt6, sounddevice, requests)..." -ForegroundColor Cyan
& $VenvPip install --upgrade pip --quiet
& $VenvPip install -r "$ScriptDir\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️ Standard install failed, trying direct package install..." -ForegroundColor Yellow
    & $VenvPip install requests sounddevice psutil python-dateutil PyQt6 PyQt6-WebEngine
}
Write-Host "✓ Dependencies installed successfully." -ForegroundColor Green

# 4. Create Desktop Shortcut
Write-Host ""
Write-Host "[3/3] Creating Windows Desktop Shortcut..." -ForegroundColor Cyan
$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath('Desktop')
$Shortcut = $WshShell.CreateShortcut("$DesktopPath\Batcomputer Companion.lnk")
$Shortcut.TargetPath = "$ScriptDir\launch-alfred.bat"
$Shortcut.WorkingDirectory = "$ScriptDir"
$Shortcut.Description = "Batcomputer BCv1.2 Voice Companion"
$Shortcut.Save()
Write-Host "✓ Desktop Shortcut created: 'Batcomputer Companion.lnk'" -ForegroundColor Green

# 5. Startup option
Write-Host ""
$Choice = Read-Host "⚡ Would you like the Batcomputer to launch automatically on Windows login? (y/N)"
if ($Choice -match "^[Yy]$") {
    $StartupPath = [System.Environment]::GetFolderPath('Startup')
    $StartupShortcut = $WshShell.CreateShortcut("$StartupPath\Batcomputer Companion.lnk")
    $StartupShortcut.TargetPath = "$ScriptDir\launch-alfred.bat"
    $StartupShortcut.WorkingDirectory = "$ScriptDir"
    $StartupShortcut.Description = "Batcomputer BCv1.2 Voice Companion"
    $StartupShortcut.Save()
    Write-Host "✓ Added to Windows Startup folder." -ForegroundColor Green
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "🦇 INSTALLATION COMPLETE // WAYNE ENTERPRISES PROTOCOLS ACTIVE" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "To launch, double click 'Batcomputer Companion' on your Desktop." -ForegroundColor White
Write-Host ""
pause
