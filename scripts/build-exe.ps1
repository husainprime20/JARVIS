#Requires -Version 5.1
<#
.SYNOPSIS
    Build the OpenJarvis Windows desktop installer (.exe) locally.

.DESCRIPTION
    Produces the same unsigned NSIS installer as the
    "Build Windows EXE" GitHub Actions workflow:

        frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/OpenJarvis_<version>_x64-setup.exe

    and the portable executable:

        frontend/src-tauri/target/x86_64-pc-windows-msvc/release/openjarvis-desktop.exe

    Prerequisites (all free):
      * Windows 10/11 x64
      * Node.js 22.22+ with npm 11.19+  (https://nodejs.org/)
      * Rust 1.89+ with the x86_64-pc-windows-msvc target  (https://rustup.rs/)
      * Git for Windows (recommended; used to fetch the Ollama sidecar)

    WebView2 is required at runtime; it ships with Windows 10/11 updates and
    is installed by the NSIS bootstrapper if missing.

.PARAMETER Version
    Optional installer version, e.g. "1.0.2". Defaults to the version in
    frontend/src-tauri/tauri.conf.json.

.PARAMETER Bundles
    Tauri bundle formats: nsis (default, produces the .exe installer), msi,
    or "nsis,msi".

.PARAMETER SkipFrontendInstall
    Skip `npm ci` (use when frontend/node_modules is already up to date).

.PARAMETER SkipOllama
    Skip the Ollama sidecar download (use when
    frontend/src-tauri/binaries/ollama-x86_64-pc-windows-msvc.exe exists).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -Version 1.0.2
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$Version = "",

    [Parameter()]
    [ValidateSet("nsis", "msi", "nsis,msi")]
    [string]$Bundles = "nsis",

    [Parameter()]
    [switch]$SkipFrontendInstall,

    [Parameter()]
    [switch]$SkipOllama
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Target = "x86_64-pc-windows-msvc"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $RepoRoot "frontend"
$TauriDir = Join-Path $Frontend "src-tauri"
$BinariesDir = Join-Path $TauriDir "binaries"
$Sidecar = Join-Path $BinariesDir "ollama-$Target.exe"
$ConfigPath = Join-Path $TauriDir "tauri.windows-exe.conf.json"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found. $Hint"
    }
}

function Invoke-Native([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "'$FilePath $($Arguments -join ' ')' failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

function Test-SemVer([string]$Value) {
    return $Value -match '^\d+\.\d+\.\d+([-.+][0-9A-Za-z.-]+)?$'
}

if (-not (Test-Path (Join-Path $RepoRoot "pyproject.toml"))) {
    throw "Run this script from a checkout of the repository (pyproject.toml not found in $RepoRoot)."
}

Write-Step "Checking prerequisites"
Assert-Command "node" "Install Node.js 22.22+ from https://nodejs.org/."
Assert-Command "npm" "npm ships with Node.js."
Assert-Command "cargo" "Install Rust 1.89+ from https://rustup.rs/ and restart the shell."
Assert-Command "rustc" "Install Rust 1.89+ from https://rustup.rs/ and restart the shell."

$nodeVersion = (& node --version).Trim().TrimStart("v")
if ([version]$nodeVersion -lt [version]"22.22.0") {
    throw "Node.js 22.22+ is required (found $nodeVersion)."
}

$npmVersion = (& npm --version).Trim()
if ([version]$npmVersion -lt [version]"11.19.0" -or [version]$npmVersion -ge [version]"12.0.0") {
    throw @"
npm 11.19+ (and below 12) is required (found $npmVersion); frontend/.npmrc sets engine-strict=true.
Run: npm install --global npm@11.19.0
Then re-open the terminal and retry.
"@
}

Write-Host "Node.js $nodeVersion, npm $npmVersion, Rust $(& rustc --version)"

Write-Step "Installing frontend dependencies"
if ($SkipFrontendInstall) {
    if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
        throw "-SkipFrontendInstall was set but frontend/node_modules does not exist."
    }
    Write-Host "Skipped (-SkipFrontendInstall)."
}
else {
    Invoke-Native -FilePath "npm" -Arguments @("ci") -WorkingDirectory $Frontend
}

Write-Step "Preparing the Ollama sidecar"
if ($SkipOllama) {
    if (-not (Test-Path $Sidecar)) {
        throw "-SkipOllama was set but $Sidecar does not exist."
    }
    Write-Host "Skipped (-SkipOllama)."
}
elseif (Test-Path $Sidecar) {
    Write-Host "Already present: $Sidecar"
}
else {
    New-Item -ItemType Directory -Force -Path $BinariesDir | Out-Null
    $bash = Get-Command "bash" -ErrorAction SilentlyContinue
    if ($bash) {
        Write-Host "Using frontend/src-tauri/scripts/download-ollama.sh"
        Invoke-Native -FilePath "bash" `
            -Arguments @("src-tauri/scripts/download-ollama.sh", $Target) `
            -WorkingDirectory $Frontend
    }
    else {
        # No Git Bash: fetch the official archive directly.
        $archive = Join-Path $env:TEMP "ollama-windows-amd64.zip"
        $extractDir = Join-Path $env:TEMP "ollama-windows-amd64"
        $url = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"
        Write-Host "Downloading $url"
        if (Get-Command "curl.exe" -ErrorAction SilentlyContinue) {
            Invoke-Native -FilePath "curl.exe" -Arguments @("-fL", $url, "-o", $archive) -WorkingDirectory $RepoRoot
        }
        else {
            Invoke-WebRequest -Uri $url -OutFile $archive
        }
        if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
        Expand-Archive -Path $archive -DestinationPath $extractDir -Force
        $exe = Join-Path $extractDir "ollama.exe"
        if (-not (Test-Path $exe)) {
            throw "ollama.exe was not found in the downloaded archive."
        }
        Copy-Item -Path $exe -Destination $Sidecar -Force
        Remove-Item -Recurse -Force $extractDir
        Remove-Item -Force $archive
    }
}

if (-not (Test-Path $Sidecar)) {
    throw "Ollama sidecar is missing: $Sidecar"
}

Write-Step "Writing unsigned Tauri build config"
$sourceConfig = Get-Content (Join-Path $TauriDir "tauri.conf.json") -Raw | ConvertFrom-Json
$effectiveVersion = if ($Version) { $Version } else { $sourceConfig.version }
if (-not (Test-SemVer $effectiveVersion)) {
    throw "Installer version must be SemVer (got '$effectiveVersion')."
}

$config = [ordered]@{
    version = $effectiveVersion
    bundle  = [ordered]@{
        externalBin           = @("binaries/ollama")
        createUpdaterArtifacts = $false
    }
}

# Write without a BOM: Tauri parses this file as JSON.
$json = $config | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Version: $effectiveVersion (bundles: $Bundles)"

Write-Step "Building (first build compiles the Rust app and can take 10-20 minutes)"
Invoke-Native -FilePath "npx" `
    -Arguments @("tauri", "build", "--target", $Target, "--bundles", $Bundles, "--config", "src-tauri/tauri.windows-exe.conf.json") `
    -WorkingDirectory $Frontend

Write-Step "Build complete"
$releaseDir = Join-Path $TauriDir "target\$Target\release"
$installers = @()
if ($Bundles -like "*nsis*") {
    $installers += Get-ChildItem -Path (Join-Path $releaseDir "bundle\nsis") -Filter "*.exe" -ErrorAction SilentlyContinue
}
if ($Bundles -like "*msi*") {
    $installers += Get-ChildItem -Path (Join-Path $releaseDir "bundle\msi") -Filter "*.msi" -ErrorAction SilentlyContinue
}

if ($installers.Count -eq 0) {
    throw "Build finished but no installer was found under $releaseDir\bundle."
}

Write-Host ""
Write-Host "Installers:" -ForegroundColor Green
$installers | ForEach-Object {
    $sizeMb = [math]::Round($_.Length / 1048576, 1)  # bytes -> MB
    Write-Host "  $($_.FullName)  ($sizeMb MB)"
}

$portable = Join-Path $releaseDir "openjarvis-desktop.exe"
if (Test-Path $portable) {
    Write-Host ""
    Write-Host "Portable executable (run in place; needs the backend on :8000):" -ForegroundColor Green
    Write-Host "  $portable"
}

Write-Host ""
Write-Host "Next: start the backend with 'jarvis serve' (or scripts\quickstart.sh), then launch the app." -ForegroundColor Yellow
