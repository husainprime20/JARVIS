#Requires -Version 5.1
<#
.SYNOPSIS
    Build the self-contained Rust/Tauri JARVIS Windows installer.

.DESCRIPTION
    Builds the native app and redesigned frontend only. JARVIS no longer ships,
    installs or starts Python or Ollama. Configure a Gemini key in Settings;
    Ollama is an optional remote/local endpoint configured in the app.

.PARAMETER Version
    Optional installer version (SemVer). Defaults to tauri.conf.json.
.PARAMETER Bundles
    Tauri bundle formats: nsis (default), msi, or nsis,msi.
.PARAMETER SkipFrontendInstall
    Skip npm ci when frontend/node_modules is already current.
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [ValidateSet("nsis", "msi", "nsis,msi")]
    [string]$Bundles = "nsis",
    [switch]$SkipFrontendInstall
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $RepoRoot "frontend"
$TauriDir = Join-Path $Frontend "src-tauri"
$Target = "x86_64-pc-windows-msvc"

function Require([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw "Missing '$Name'. $Hint" }
}
function Run([string]$Name, [string[]]$Args, [string]$Cwd) {
    Push-Location $Cwd
    try {
        & $Name @Args
        if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE." }
    } finally { Pop-Location }
}

Require "node" "Install Node.js 22.22+ from https://nodejs.org/."
Require "npm" "Install npm 11.19.x with 'npm install --global npm@11.19.0'."
Require "cargo" "Install Rust 1.89+ and the $Target target."
Require "rustc" "Install Rust 1.89+ from https://rustup.rs/."
if (-not $SkipFrontendInstall) { Run "npm" @("ci") $Frontend }
elseif (-not (Test-Path (Join-Path $Frontend "node_modules"))) { throw "node_modules is missing; omit -SkipFrontendInstall." }

$config = Get-Content (Join-Path $TauriDir "tauri.conf.json") -Raw | ConvertFrom-Json
$effectiveVersion = if ($Version) { $Version } else { $config.version }
if ($effectiveVersion -notmatch '^\d+\.\d+\.\d+([-.+][0-9A-Za-z.-]+)?$') { throw "Invalid SemVer version '$effectiveVersion'." }
$overridePath = Join-Path $TauriDir "tauri.windows-exe.conf.json"
$override = @{ version = $effectiveVersion; bundle = @{ createUpdaterArtifacts = $false } } | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($overridePath, $override, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Building JARVIS $effectiveVersion for Windows x64 (Rust/Tauri; no Python or Ollama)." -ForegroundColor Cyan
Run "npx" @("tauri", "build", "--target", $Target, "--bundles", $Bundles, "--config", "src-tauri/tauri.windows-exe.conf.json") $Frontend

$release = Join-Path $TauriDir "target\$Target\release\bundle"
$installers = @()
if ($Bundles -like "*nsis*") { $installers += Get-ChildItem (Join-Path $release "nsis") -Filter "*.exe" -ErrorAction SilentlyContinue }
if ($Bundles -like "*msi*") { $installers += Get-ChildItem (Join-Path $release "msi") -Filter "*.msi" -ErrorAction SilentlyContinue }
if ($installers.Count -eq 0) { throw "No installer found under $release." }
$installers | ForEach-Object { Write-Host ("{0} ({1:N1} MB)" -f $_.FullName, ($_.Length / 1MB)) -ForegroundColor Green }
Write-Host "Install and open JARVIS. Add a Gemini API key in Settings; use Ctrl+Alt+J to summon it." -ForegroundColor Cyan
