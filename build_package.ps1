$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version = (python -c "from app_info import APP_VERSION; print(APP_VERSION)").Trim()
if (!$Version) {
    throw "Unable to read APP_VERSION from app_info.py"
}
$AppName = "CourseReminder"
$ReleaseDir = Join-Path $Root "release"
$DistDir = Join-Path $Root "dist"
$BuildDir = Join-Path $Root "build"
$PackageName = "CourseReminder-v$Version-windows.zip"
$PackagePath = Join-Path $ReleaseDir $PackageName

Set-Location $Root

$PythonBase = (python -c "import sys; print(sys.base_prefix)").Trim()
$TclSource = Join-Path $PythonBase "tcl\tcl8.6"
$TkSource = Join-Path $PythonBase "tcl\tk8.6"
$TclWorkRoot = Join-Path $Root "work\pyinstaller-tcl"
$TclWork = Join-Path $TclWorkRoot "tcl8.6"
$TkWork = Join-Path $TclWorkRoot "tk8.6"

if (!(Test-Path (Join-Path $TclSource "init.tcl")) -or !(Test-Path (Join-Path $TkSource "tk.tcl"))) {
    throw "The selected Python runtime does not include a complete Tcl/Tk library."
}
if (Test-Path $TclWorkRoot) {
    Remove-Item -LiteralPath $TclWorkRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $TclWorkRoot | Out-Null
Copy-Item -LiteralPath $TclSource -Destination $TclWork -Recurse
Copy-Item -LiteralPath $TkSource -Destination $TkWork -Recurse

$InitPath = Join-Path $TclWork "init.tcl"
$InitText = Get-Content -LiteralPath $InitPath -Raw
$InitText = $InitText -replace 'package require -exact Tcl ([0-9.]+)', 'package require Tcl $1'
$InitText | Set-Content -LiteralPath $InitPath -Encoding Ascii
$env:TCL_LIBRARY = $TclWork
$env:TK_LIBRARY = $TkWork

if (!(Test-Path "assets\phoebe_pet.gif")) {
    throw "Missing assets\phoebe_pet.gif"
}

if (Test-Path $DistDir) {
    Remove-Item $DistDir -Recurse -Force
}
if (Test-Path $BuildDir) {
    Remove-Item $BuildDir -Recurse -Force
}
if (!(Test-Path $ReleaseDir)) {
    New-Item -ItemType Directory $ReleaseDir | Out-Null
}
if (Test-Path $PackagePath) {
    Remove-Item $PackagePath -Force
}

python -m PyInstaller .\CourseReminder.spec --noconfirm --clean

$ReadmePath = Join-Path (Join-Path $DistDir $AppName) "README_RUN.txt"
$ReadmeLines = @(
    "CourseReminder v$Version",
    "",
    "Run:",
    "Double-click CourseReminder.exe",
    "",
    "Notes:",
    "1. Personal data is saved under Windows AppData, not inside this package.",
    "2. DeepSeek API Key must be entered by each user in the AI assistant window.",
    "3. If Windows Defender warns about an unknown app, choose allow/trust if you trust this package."
)
$ReadmeLines | Set-Content -LiteralPath $ReadmePath -Encoding UTF8

Compress-Archive -Path (Join-Path $DistDir $AppName) -DestinationPath $PackagePath -Force

Write-Host "Package created: $PackagePath"
