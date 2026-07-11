$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version = "1.0.1"
$AppName = "CourseReminder"
$ReleaseDir = Join-Path $Root "release"
$DistDir = Join-Path $Root "dist"
$BuildDir = Join-Path $Root "build"
$PackageName = "CourseReminder-v$Version-windows.zip"
$PackagePath = Join-Path $ReleaseDir $PackageName

Set-Location $Root

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
