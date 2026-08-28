# Setup for Windows. macOS/Linux: run setup.sh instead.
#
# If PowerShell refuses to run this, it is the execution policy, not the
# script. Either right-click -> "Run with PowerShell", or run:
#     powershell -ExecutionPolicy Bypass -File setup.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --- ffmpeg -----------------------------------------------------------------
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: ffmpeg not found on PATH." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Install it with either:"
    Write-Host "    winget install Gyan.FFmpeg"
    Write-Host "    choco install ffmpeg"
    Write-Host ""
    Write-Host "  Then OPEN A NEW TERMINAL - PATH changes do not apply to this one."
    exit 1
}

# --- Python -----------------------------------------------------------------
# The `py` launcher ships with python.org installs and is the reliable way to
# pin a version on Windows; fall back to whatever `python` resolves to.
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    try { py -3.11 --version *>$null; $py = @("py", "-3.11") } catch { $py = @("py", "-3") }
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $py = @("python")
} else {
    Write-Host "ERROR: Python not found. Install 3.11 from python.org" -ForegroundColor Red
    Write-Host "       Tick 'Add Python to PATH' during installation." -ForegroundColor Red
    exit 1
}

Write-Host "Creating virtual environment..."
& $py[0] $py[1..($py.Length-1)] -m venv .venv

# Windows venvs put executables in Scripts\, not bin/.
$vpy = ".\.venv\Scripts\python.exe"

Write-Host "Installing dependencies (this takes a minute)..."
& $vpy -m pip install --quiet --upgrade pip
& $vpy -m pip install --quiet -r requirements.txt

if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "  1. Check it works (no credentials needed):"
Write-Host "       .\.venv\Scripts\python.exe -m pytest"
Write-Host ""
Write-Host "  2. Configure it:"
Write-Host "       .\.venv\Scripts\python.exe setup_wizard.py"
Write-Host ""
Write-Host "  3. Run it:"
Write-Host "       .\.venv\Scripts\python.exe run.py --max 5 --publisher local --brand generic"
Write-Host ""
Write-Host "  To host the always-on sheet watcher:"
Write-Host "       .\.venv\Scripts\python.exe watch.py --sheet <SHEET_ID> --publisher gdrive"
Write-Host ""
Write-Host "The pipeline runs with no credentials at all. See docs\INSTALL.md" -ForegroundColor DarkGray
Write-Host "for adding an LLM key and Google Drive/Sheets access." -ForegroundColor DarkGray
Write-Host ""
