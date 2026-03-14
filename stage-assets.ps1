#Requires -Version 5.1
<#
.SYNOPSIS
    Stage Homeworld game assets from Steam (or another source) into the
    HomeworldSDL data directory and verify homeworld.big's SHA-256.

.PARAMETER Source
    Path to the Homeworld1Classic\Data folder.
    Defaults to D:\SteamLibrary\steamapps\common\Homeworld\Homeworld1Classic\Data

.PARAMETER Dest
    Destination assets folder. Defaults to .\assets  (relative to this script).

.PARAMETER ExpectedHash
    SHA-256 of the canonical homeworld.big. Leave blank to skip verification.

.EXAMPLE
    # Use defaults (your Steam library):
    .\stage-assets.ps1

    # Specify a GOG install:
    .\stage-assets.ps1 -Source "C:\GOG Games\Homeworld\Homeworld1Classic\Data"
#>
param(
    [string]$Source       = 'D:\SteamLibrary\steamapps\common\Homeworld\Homeworld1Classic\Data',
    [string]$Dest         = (Join-Path $PSScriptRoot 'assets'),
    [string]$ExpectedHash = 'e38c0528c1d4bd9d9195d26d5231ae29bef18f57d9bd1fe2eed33fb2b9b172a8'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK([string]$msg)   { Write-Host "    [OK] $msg"   -ForegroundColor Green  }
function Write-Warn([string]$msg) { Write-Host "    [WARN] $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red    }

# ── Validate source ───────────────────────────────────────────────────────────
Write-Step "Validating asset source: $Source"

if (-not (Test-Path $Source)) {
    throw "Source path not found: $Source`nCheck that Homeworld Remastered Collection is installed in your Steam library."
}

# Required files (relative to Data/)
$required = @(
    'homeworld.big',
    'HW_Comp.vce',
    'HW_Music.wxd'
)

$missing = @()
foreach ($f in $required) {
    $full = Join-Path $Source $f
    if (-not (Test-Path $full)) { $missing += $f }
}

if ($missing.Count -gt 0) {
    Write-Fail "Missing required files in $Source :"
    $missing | ForEach-Object { Write-Host "      $_" -ForegroundColor Red }
    throw 'Required assets are missing. Ensure Homeworld1Classic is fully installed.'
}
Write-OK 'All required files present in source'

# ── Create destination ────────────────────────────────────────────────────────
Write-Step "Preparing destination: $Dest"
if (-not (Test-Path $Dest)) {
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    Write-OK "Created $Dest"
} else {
    Write-OK "$Dest already exists"
}

# ── Copy files ────────────────────────────────────────────────────────────────
Write-Step 'Copying required asset files'

foreach ($f in $required) {
    $src  = Join-Path $Source $f
    $dst  = Join-Path $Dest   $f
    Write-Host "    $f …" -NoNewline
    Copy-Item -Path $src -Destination $dst -Force
    $size = (Get-Item $dst).Length
    Write-Host " $('{0:N1}' -f ($size / 1MB)) MB" -ForegroundColor Green
}

# ── Copy movies (optional) ────────────────────────────────────────────────────
Write-Step 'Copying optional movies folder'

$moviesSource = Join-Path $Source 'movies'
$moviesDest   = Join-Path $Dest   'movies'

if (Test-Path $moviesSource) {
    Write-Host "    Copying movies\ …" -NoNewline
    Copy-Item -Path $moviesSource -Destination $moviesDest -Recurse -Force
    $count = (Get-ChildItem $moviesDest -Recurse -File).Count
    Write-Host " $count files" -ForegroundColor Green
    Write-OK 'Movies copied'
} else {
    Write-Warn "movies\ not found in source — cutscenes will not play (non-critical)"
}

# ── Also copy Update.big if present (patched assets) ─────────────────────────
$updateBig = Join-Path $Source 'Update.big'
if (Test-Path $updateBig) {
    Write-Step 'Copying Update.big (patch data)'
    Copy-Item -Path $updateBig -Destination (Join-Path $Dest 'Update.big') -Force
    Write-OK 'Update.big copied'
}

# ── SHA-256 verification ──────────────────────────────────────────────────────
Write-Step 'Verifying homeworld.big SHA-256'

$bigFile = Join-Path $Dest 'homeworld.big'
Write-Host '    Computing hash (may take ~10 seconds)…' -ForegroundColor Yellow

$hash = (Get-FileHash -Path $bigFile -Algorithm SHA256).Hash.ToLower()
Write-Host "    Got : $hash"

if ([string]::IsNullOrWhiteSpace($ExpectedHash)) {
    Write-Warn 'No expected hash provided — skipping verification'
} elseif ($hash -eq $ExpectedHash.ToLower()) {
    Write-OK 'homeworld.big SHA-256 matches — assets are authentic'
} else {
    Write-Host "    Want: $ExpectedHash" -ForegroundColor Red
    Write-Warn 'Hash mismatch. This may be a different version of the assets (e.g. GOG vs Steam).'
    Write-Warn 'The game may still work, but verify your Homeworld installation is complete and unmodified.'
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host '─────────────────────────────────────────────' -ForegroundColor Cyan
Write-Host '  Asset staging complete' -ForegroundColor White
Write-Host '─────────────────────────────────────────────' -ForegroundColor Cyan
Write-Host ''
Write-Host "  Source : $Source"
Write-Host "  Dest   : $Dest"
Write-Host ''
(Get-ChildItem $Dest -File | Select-Object Name, @{N='Size';E={'{0:N1} MB' -f ($_.Length/1MB)}}) |
    Format-Table -AutoSize | Out-String | Write-Host
Write-Host ''
Write-Host '  Set HW_Data to the Dest path above (launch.bat does this automatically).' -ForegroundColor Green
Write-Host ''
