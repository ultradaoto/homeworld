#Requires -Version 5.1
<#
.SYNOPSIS  HomeworldSDL — Windows build & setup (PS 5.1 compatible, robust MSYS2 invocation)
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Paths ─────────────────────────────────────────────────────────────────────
$ProjectRoot        = $PSScriptRoot
$RepoDir            = Join-Path $ProjectRoot 'HomeworldSDL'
$BuildDir           = Join-Path $RepoDir 'build'
$AssetsDir          = Join-Path $ProjectRoot 'assets'
$LaunchScript       = Join-Path $ProjectRoot 'launch.bat'
$DefaultSteamAssets = 'D:\SteamLibrary\steamapps\common\Homeworld\Homeworld1Classic\Data'
$HomeworldBigHash   = 'e38c0528c1d4bd9d9195d26d5231ae29bef18f57d9bd1fe2eed33fb2b9b172a8'
$Msys2Root          = 'C:\msys64'
$Msys2Bash          = "$Msys2Root\usr\bin\bash.exe"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK([string]$msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "    [WARN] $msg" -ForegroundColor Yellow }

# Windows path → MSYS2 unix path:  D:\Foo\Bar → /d/Foo/Bar
function To-Unix([string]$p) {
    $p = $p -replace '\\', '/'
    if ($p -match '^([A-Za-z]):(.*)') {
        $p = '/' + $matches[1].ToLower() + $matches[2]
    }
    return $p
}

# Write a bash script to a temp file and run it under MSYS2 MinGW64.
# Using a file avoids ALL quoting/escaping issues with -ArgumentList.
function Invoke-Msys2 {
    param([string]$Script, [switch]$AllowFail)

    # Ensure temp dir exists
    $tmpDir = "$Msys2Root\tmp"
    if (-not (Test-Path $tmpDir)) { New-Item -ItemType Directory $tmpDir | Out-Null }

    $guid    = [System.Guid]::NewGuid().ToString('N')
    $winPath = "$tmpDir\hw_$guid.sh"
    $unixPath = To-Unix $winPath

    # Write script — LF line endings required for bash
    $lines = @(
        '#!/bin/bash'
        'export MSYSTEM=MINGW64'
        'source /etc/profile >/dev/null 2>&1'
        'set -euo pipefail'
        $Script
    )
    # UTF-8 WITHOUT BOM — bash chokes on the BOM if present
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($winPath,
        ($lines -join "`n") + "`n",
        $utf8NoBom)

    $env:MSYSTEM = 'MINGW64'
    $proc = Start-Process -FilePath $Msys2Bash `
                          -ArgumentList '--login', $unixPath `
                          -Wait -PassThru -NoNewWindow

    Remove-Item $winPath -Force -ErrorAction SilentlyContinue

    if ((-not $AllowFail) -and ($proc.ExitCode -ne 0)) {
        throw "MSYS2 script failed (exit $($proc.ExitCode)).`nScript was:`n$Script"
    }
    return $proc.ExitCode
}

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host "`n  HomeworldSDL Windows Setup`n  Project: $ProjectRoot`n" -ForegroundColor White

# ── Step 1 — MSYS2 ───────────────────────────────────────────────────────────
Write-Step 'Checking MSYS2'
if (-not (Test-Path $Msys2Bash)) {
    Write-Warn 'MSYS2 not found — installing via winget'
    winget install --id MSYS2.MSYS2 --silent --accept-source-agreements --accept-package-agreements
    if (-not (Test-Path $Msys2Bash)) {
        throw "MSYS2 install failed. Install manually from https://www.msys2.org/ to C:\msys64"
    }
}
Write-OK "MSYS2 found at $Msys2Root"

# ── Step 2 — pacman packages ─────────────────────────────────────────────────
Write-Step 'Updating pacman & installing MinGW-w64 packages'
# Two-pass update (first updates pacman itself)
Invoke-Msys2 'pacman -Syuu --noconfirm' -AllowFail | Out-Null
Invoke-Msys2 'pacman -Syuu --noconfirm' -AllowFail | Out-Null

$pkgs = @(
    'mingw-w64-x86_64-gcc'
    'mingw-w64-x86_64-meson'
    'mingw-w64-x86_64-ninja'
    'mingw-w64-x86_64-SDL2'
    'mingw-w64-x86_64-ffmpeg'
    'flex'
    'bison'
    'git'
) -join ' '

Invoke-Msys2 "pacman -S --needed --noconfirm $pkgs"
Write-OK 'All packages ready'

# ── Step 3 — Clone ───────────────────────────────────────────────────────────
Write-Step 'Cloning HomeworldSDL'
$repoDirUnix = To-Unix $RepoDir

if (Test-Path (Join-Path $RepoDir '.git')) {
    Write-OK 'Already cloned — pulling latest'
    Invoke-Msys2 "git -C '$repoDirUnix' pull --ff-only"
} else {
    # Delete partial clone if it exists
    if (Test-Path $RepoDir) { Remove-Item $RepoDir -Recurse -Force }

    Invoke-Msys2 @"
git clone https://github.com/HomeworldSDL/HomeworldSDL.git '$repoDirUnix'
echo "Clone exit: $?"
ls '$repoDirUnix/meson.build'
"@
    if (-not (Test-Path (Join-Path $RepoDir 'meson.build'))) {
        throw "meson.build not found in $RepoDir after clone — the clone failed or went to the wrong place."
    }
    Write-OK "Cloned to $RepoDir"
}

# ── Step 4 — Meson configure ─────────────────────────────────────────────────
Write-Step 'Configuring with Meson'
$buildDirUnix = To-Unix $BuildDir

# Always pass source + build dir. --wipe cleans a stale/partial build dir.
$mesonFlags = "--buildtype=release -Db_sanitize=none -Ddebug_asserts=false -Dmovies=true"
if (Test-Path (Join-Path $BuildDir 'build.ninja')) {
    Invoke-Msys2 "meson setup --reconfigure '$repoDirUnix' '$buildDirUnix' $mesonFlags"
} else {
    # Remove empty/broken build dir so meson starts clean
    if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }
    Invoke-Msys2 "meson setup '$repoDirUnix' '$buildDirUnix' $mesonFlags"
}
Write-OK 'Meson configured'

# ── Step 5 — Compile ─────────────────────────────────────────────────────────
Write-Step 'Compiling'
$cpuCount = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
Invoke-Msys2 "meson compile -C '$buildDirUnix' -j $cpuCount"
Write-OK 'Compile done'

# Find the binary
$binaryPath = Get-ChildItem -Path $BuildDir -Filter 'homeworld.exe' -Recurse -ErrorAction SilentlyContinue |
              Select-Object -First 1 -ExpandProperty FullName
if (-not $binaryPath) {
    throw "homeworld.exe not found under $BuildDir. Check the compiler output above."
}
Write-OK "Binary: $binaryPath"

# ── Step 6 — Copy runtime DLLs ───────────────────────────────────────────────
Write-Step 'Copying runtime DLLs'
$binDir = Split-Path $binaryPath
$dllSrc = "$Msys2Root\mingw64\bin"
# Glob all FFmpeg, SDL2, and MinGW runtime DLLs — version-number agnostic
# Copy every DLL from MSYS2 — covers all transitive FFmpeg/MinGW deps in one shot
$allDlls = Get-ChildItem $dllSrc -Filter '*.dll'
$allDlls | Copy-Item -Destination $binDir -Force
Write-OK "$($allDlls.Count) DLLs copied from MSYS2"

# ── Step 7 — Asset staging ────────────────────────────────────────────────────
Write-Step 'Asset staging'
Write-Host ''
Write-Host "  Default Steam path: $DefaultSteamAssets" -ForegroundColor Yellow
$userInput   = Read-Host '  Press ENTER to use it, or type another path'
$AssetSource = if ([string]::IsNullOrWhiteSpace($userInput)) { $DefaultSteamAssets } else { $userInput.Trim() }

if (-not (Test-Path $AssetSource)) {
    Write-Warn "Path not found: $AssetSource — skipping. Run stage-assets.ps1 later."
} else {
    Write-Host "  Copy from : $AssetSource" -ForegroundColor Yellow
    Write-Host "  Copy to   : $AssetsDir" -ForegroundColor Yellow
    $confirm = Read-Host '  Type YES to copy'
    if ($confirm -eq 'YES') {
        & "$PSScriptRoot\stage-assets.ps1" -Source $AssetSource -Dest $AssetsDir -ExpectedHash $HomeworldBigHash
    } else {
        Write-Warn 'Skipped — run stage-assets.ps1 separately.'
    }
}

# ── Step 8 — Write launch.bat ─────────────────────────────────────────────────
Write-Step 'Writing launch scripts'
@"
@echo off
REM HomeworldSDL — generated by setup.ps1
set HW_Data=$AssetsDir
if not exist "$binaryPath" ( echo Binary not found. Re-run setup.ps1. & pause & exit /b 1 )
if not exist "%HW_Data%\homeworld.big" ( echo Assets not found in %HW_Data%. Run stage-assets.ps1. & pause & exit /b 1 )
echo Starting HomeworldSDL  [Data: %HW_Data%]
"$binaryPath" %*
if errorlevel 1 ( echo. & echo Exited with error. Try launch-windowed.bat & pause )
"@ | Set-Content -Path $LaunchScript -Encoding ASCII

@"
@echo off
REM Windowed mode — safer for GPU/resolution troubleshooting
call "%~dp0launch.bat" /window %*
"@ | Set-Content -Path (Join-Path $ProjectRoot 'launch-windowed.bat') -Encoding ASCII
Write-OK 'launch.bat and launch-windowed.bat written'

# ── Step 9 — Smoke test ───────────────────────────────────────────────────────
Write-Step 'Smoke test'
$env:HW_Data = $AssetsDir
$proc = Start-Process -FilePath $binaryPath -PassThru -ErrorAction SilentlyContinue
Start-Sleep -Seconds 4
if ($null -ne $proc) {
    if (-not $proc.HasExited) {
        Write-OK 'Game window opened successfully'
        $proc.Kill()
    } elseif ($proc.ExitCode -eq 0) {
        Write-OK 'Binary ran cleanly'
    } else {
        Write-Warn "Binary exited immediately (code $($proc.ExitCode)) — stage assets if not done yet"
    }
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ''
Write-Host '══════════════════════════════════════' -ForegroundColor Cyan
Write-Host '  Setup Complete' -ForegroundColor White
Write-Host '══════════════════════════════════════' -ForegroundColor Cyan
Write-Host "  Binary  : $binaryPath"
Write-Host "  Assets  : $AssetsDir"
Write-Host "  Launch  : $LaunchScript"
Write-Host ''
Write-Host '  To play         : launch.bat' -ForegroundColor Green
Write-Host '  Troubleshoot    : launch-windowed.bat' -ForegroundColor Yellow
Write-Host '  Known quirks    : disable Hit Effects if game crashes near combat'
Write-Host ''
