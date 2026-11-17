<#
  Kelo installer for Windows (Docker Desktop with WSL 2). macOS and Linux: scripts/install.sh

    irm https://kelo.chat/install.ps1 | iex                       install, or update when Kelo is already there
    & ([scriptblock]::Create((irm <address>/install.ps1))) -Yes         no questions (defaults everywhere)
    .\install.ps1 -Uninstall                                             remove Kelo (your data is kept unless -DeleteData)

  What it does, in order: checks this PC (Windows version, CPU type, WSL 2), checks Docker Desktop and Compose (and says how to
  get them), installs `uv` if it is missing, puts the Kelo files in %USERPROFILE%\kelo, prepares Python with uv, adds a `kelo`
  command, pulls Kelo's official images (signed; checked before use), and opens the guided setup, which starts Kelo and shows
  a QR code for your phone. Running it again is an update: .env, chats, keys and phones stay as they are.

  Parameters (each also as an environment variable):
    -Yes          answer every question with its default (KELO_YES=1); never deletes data
    -Dir DIR      where Kelo lives (KELO_HOME, default %USERPROFILE%\kelo)
    -Source SRC   where to get the Kelo files: a folder, a .zip / .tar.gz URL or a git URL (KELO_SOURCE). A custom source is
                  built here, since its images are not the official ones, unless -FromRelease is given too
    -FromRelease  the default: pull the signed official images instead of building them here (KELO_FROM_RELEASE=1). If the
                  pull fails they are built here instead, with a warning. The Claude/agy containers are always built here
                  (their CLIs are never published)
    -BuildHere    build the images on this PC instead (KELO_FROM_RELEASE=0); slow, and it can fail on small machines
    -Version X.Y.Z  install exactly this release instead of the newest (KELO_VERSION): its files from the release-X.Y.Z tag of
                  kelo-chat/kelo-server, and with -FromRelease the signed images of that version. A version that does not exist
                  stops the installer before anything changes (all versions: the releases page of github.com/kelo-chat/kelo-server).
                  Signed images of an older version need that version to know `--release-version`; otherwise its images are
                  built here from its files, with a warning
    -AllowDowngrade  an update to a version OLDER than the installed one is refused without this (KELO_ALLOW_DOWNGRADE=1):
                  data written by a newer version may not go back
    -NoStart      save the answers but do not start the containers (KELO_NO_START=1)
    -AutoUpdate   let Kelo update the server by itself at night (KELO_AUTO_UPDATE=1); -NoAutoUpdate: only when the owner
                  presses Update in the app (the default; asked when there is a terminal)
    -Uninstall    stop and remove Kelo; add -DeleteData to also delete chats, keys and files for good
  NO_COLOR=1 turns colours off.
#>
[CmdletBinding()]
param(
  [switch]$Yes,
  [string]$Dir,
  [string]$Source,
  [switch]$NoStart,
  [string]$Version,
  [switch]$AllowDowngrade,
  [switch]$FromRelease,
  [switch]$BuildHere,
  [switch]$AutoUpdate,
  [switch]$NoAutoUpdate,
  [switch]$Uninstall,
  [switch]$DeleteData
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

if (-not $Dir) { $Dir = if ($env:KELO_HOME) { $env:KELO_HOME } else { Join-Path $env:USERPROFILE 'kelo' } }
if (-not $Source -and $env:KELO_SOURCE) { $Source = $env:KELO_SOURCE }
if (-not $Version -and $env:KELO_VERSION) { $Version = $env:KELO_VERSION }
if ($env:KELO_ALLOW_DOWNGRADE -eq '1') { $AllowDowngrade = $true }
$ReleasesBase = if ($env:KELO_RELEASES_BASE) { $env:KELO_RELEASES_BASE } else { 'https://github.com/kelo-chat/kelo-server' }   # the public source repository (tests point it at a local server)
if ($env:KELO_YES -eq '1') { $Yes = $true }
if ($env:KELO_NO_START -eq '1') { $NoStart = $true }
if ($env:KELO_FROM_RELEASE -eq '1') { $FromRelease = $true }
if ($env:KELO_FROM_RELEASE -eq '0') { $BuildHere = $true }
# The official images are the default. A custom -Source is not the release, so it is built here unless -FromRelease is given.
$releaseAsked = [bool]$FromRelease
if ($BuildHere -or (-not $releaseAsked -and $Source)) { $FromRelease = $false } else { $FromRelease = $true }
if ($env:KELO_AUTO_UPDATE -eq '1') { $AutoUpdate = $true } elseif ($env:KELO_AUTO_UPDATE -eq '0') { $NoAutoUpdate = $true }
$BinDir = if ($env:KELO_BIN_DIR) { $env:KELO_BIN_DIR } else { Join-Path $env:USERPROFILE '.local\bin' }

# ------------------------------------------------------------------------------------------------------------- language
$culture = if ($env:KELO_LANG) { $env:KELO_LANG } else { (Get-UICulture).Name }
$Ko = $culture -like 'ko*'
function Say([string]$en, [string]$ko) { if ($Ko) { $ko } else { $en } }

# ------------------------------------------------------------------------------------------------------------- look
$Color = -not $env:NO_COLOR -and -not [Console]::IsOutputRedirected
$Interactive = -not $Yes -and -not [Console]::IsInputRedirected -and [Environment]::UserInteractive

function W([string]$text, [string]$fg) {
  if ($Color -and $fg) { Write-Host $text -ForegroundColor $fg -NoNewline } else { Write-Host $text -NoNewline }
}
function Ok([string]$t)   { W '  ✓ ' 'Green'; Write-Host $t }
function Warn([string]$t) { W '  ! ' 'Yellow'; W $t 'Yellow'; Write-Host '' }
function Note([string]$t) { W "    $t" 'DarkGray'; Write-Host '' }
function Section([string]$t) { Write-Host ''; W '› ' 'Cyan'; Write-Host $t }
# `exit` would close the whole PowerShell window when this runs through `irm | iex`, so stopping is a private exception that
# the last lines of this file catch.
class KeloStop : System.Exception { [int]$Code; KeloStop([int]$code) : base('kelo-stop') { $this.Code = $code } }
function Stop-Install([int]$code) { throw [KeloStop]::new($code) }
function Die([string]$t) { Write-Host ''; W '  ✗ ' 'Red'; Write-Host $t; Write-Host ''; Stop-Install 1 }

# Kelo in half blocks: same drawing as the setup screen (blue body, green leaf, yellow beak).
$Mascot = @('......GG......', '.....GGG......', '....BBBBBB....', '..BBBBBBBBBB..', '.BBBBBBBBBBBB.', '.BBKBBBBBBKBB.', '.BBBBBYYBBBBB.',
            'DBBBBBYYBBBBBD', 'DBBBbbbbbbBBBD', '.BBbbbbbbbbBB.', '.BBbbbbbbbbBB.', '..BBBbbbbBBB..', '....L....L....', '...LL...LL....')
function Colour-Of([string]$c) {   # switch -CaseSensitive: 'B' (body) and 'b' (belly) are different colours
  switch -CaseSensitive ($c) { 'G' { 71 } 'B' { 39 } 'b' { 153 } 'D' { 32 } 'K' { 234 } 'Y' { 214 } 'L' { 109 } default { $null } }
}

function Show-Banner {
  $title = 'Kelo ' + (Say 'installer' '설치')
  $sub = Say 'Your personal assistant, on a server you run.' '내가 운영하는 서버에서 돌아가는 나만의 비서.'
  Write-Host ''
  $vt = $Color -and $Host.UI.SupportsVirtualTerminal
  if (-not $vt) { Write-Host "  $title"; Write-Host "  $sub"; return }
  $e = [char]27
  for ($i = 0; $i -lt $Mascot.Count; $i += 2) {
    $top = $Mascot[$i]; $bot = $Mascot[$i + 1]; $line = '  '
    for ($j = 0; $j -lt $top.Length; $j++) {
      $a = [string]$top[$j]; $b = [string]$bot[$j]
      $ca = Colour-Of $a; $cb = Colour-Of $b
      if ($ca -and $cb) { $line += "$e[38;5;${ca};48;5;${cb}m▀$e[0m" }
      elseif ($ca) { $line += "$e[38;5;${ca}m▀$e[0m" }
      elseif ($cb) { $line += "$e[38;5;${cb}m▄$e[0m" }
      else { $line += ' ' }
    }
    if ($i -eq 2) { $line += "    $e[1;38;5;39m$title$e[0m" }
    if ($i -eq 4) { $line += "    $sub" }
    Write-Host $line
  }
}

function Ask-YesNo([string]$q, [bool]$default) {
  if (-not $Interactive) { return $default }
  $hint = if ($default) { 'Y/n' } else { 'y/N' }
  while ($true) {
    $a = Read-Host "  $q [$hint]"
    if (-not $a) { return $default }
    if ($a -match '^(y|yes|예)$') { return $true }
    if ($a -match '^(n|no|아니요)$') { return $false }
  }
}

$Log = Join-Path $env:TEMP ("kelo-install-{0}.log" -f [guid]::NewGuid().ToString('N').Substring(0, 8))

# Step "what is happening" { command }: one line with a spinner; output goes to the log, shown only if it fails. -Soft: a failed
# step returns instead of stopping the install (the caller falls back); its exit code is left in $script:StepCode.
function Step([string]$label, [scriptblock]$block, [switch]$Soft) {
  $start = Get-Date
  Add-Content -Path $Log -Value "`n==> $label"
  $job = Start-Job -ArgumentList $block.ToString(), $Log, (Get-Location).Path -ScriptBlock {
    param($b, $log, $loc)
    Set-Location $loc
    $global:LASTEXITCODE = 0
    try { & ([scriptblock]::Create($b)) *>> $log } catch { Add-Content -Path $log -Value $_.ToString(); return 1 }
    if ($LASTEXITCODE) { return [int]$LASTEXITCODE } else { return 0 }
  }
  $frames = '⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'; $i = 0
  while ($job.State -eq 'Running' -or $job.State -eq 'NotStarted') {
    if ($Color) {
      $secs = [int]((Get-Date) - $start).TotalSeconds
      Write-Host ("`r  {0} {1} ({2}s)    " -f $frames[$i % 10], $label, $secs) -NoNewline -ForegroundColor Cyan
    }
    Start-Sleep -Milliseconds 120; $i++
  }
  if ($Color) { Write-Host ("`r" + (' ' * ($label.Length + 20)) + "`r") -NoNewline }
  $code = 1
  try { $code = [int](@(Receive-Job $job -ErrorAction Stop) | Select-Object -Last 1) } catch { Add-Content -Path $Log -Value $_.ToString() }
  Remove-Job $job -Force
  $secs = [int]((Get-Date) - $start).TotalSeconds
  $script:StepCode = $code
  if ($code -eq 0) { Ok "$label ($secs s)"; return }
  W '  ✗ ' 'Red'; Write-Host $label
  if ($Soft) { return }
  Get-Content $Log -Tail 20 | ForEach-Object { W '    │ ' 'DarkGray'; Write-Host $_ }
  Die (Say "That step failed (exit $code). Full log: $Log" "이 단계가 실패했습니다 (종료 코드 $code). 전체 로그: $Log")
}

function Docker-Screen([string]$head, [string[]]$lines) {
  Write-Host ''
  W "  ╭─ $head" 'Red'; Write-Host ''
  foreach ($l in $lines) { if ($l) { W '  │  ' 'Red'; Write-Host $l } }
  W '  │' 'Red'; Write-Host ''
  W '  │  ' 'Red'; Write-Host (Say 'Then run the same command again; it picks up where it stopped.' '그다음 같은 명령을 다시 실행하면 이어서 진행합니다.')
  W '  ╰─' 'Red'; Write-Host ''; Write-Host ''
  Stop-Install 1
}

# ------------------------------------------------------------------------------------------------------------- this PC
function Test-Platform {
  $os = [Environment]::OSVersion.Version
  if ($os.Major -lt 10) { Die (Say 'Kelo needs Windows 10 or 11 (Docker Desktop with WSL 2).' 'Kelo에는 Windows 10 또는 11이 필요합니다 (Docker Desktop과 WSL 2).') }
  $script:Arch = switch ($env:PROCESSOR_ARCHITECTURE) { 'AMD64' { 'x86_64' } 'ARM64' { 'arm64' } default { $env:PROCESSOR_ARCHITECTURE } }
  $script:Platform = "Windows $($os.Major).$($os.Build)"
}

function Test-Docker {
  $docker = Get-Command docker -ErrorAction SilentlyContinue
  if (-not $docker) {
    $wsl = Get-Command wsl -ErrorAction SilentlyContinue
    Docker-Screen (Say 'Docker is needed' 'Docker가 필요합니다') @(
      (Say 'Kelo runs in Docker, so Docker comes first.' 'Kelo는 Docker 안에서 동작하므로 Docker가 먼저 필요합니다.'),
      (Say 'Install Docker Desktop (free for personal use):' 'Docker Desktop을 설치하세요 (개인 사용 무료):'),
      '  https://docs.docker.com/desktop/setup/install/windows-install/',
      $(if (Get-Command winget -ErrorAction SilentlyContinue) { Say 'or:  winget install Docker.DockerDesktop' '또는:  winget install Docker.DockerDesktop' }),
      $(if (-not $wsl) { Say 'WSL 2 first:  wsl --install   (then restart Windows)' '먼저 WSL 2:  wsl --install   (그다음 Windows 다시 시작)' }),
      (Say 'Turn on WSL 2 when Docker Desktop asks for it.' 'Docker Desktop이 물으면 WSL 2를 켜 주세요.'))
  }
  Ok ("Docker " + ((& docker version --format '{{.Client.Version}}' 2>$null) | Select-Object -First 1))
  $compose = (& docker compose version --short 2>$null)
  if ($LASTEXITCODE -ne 0 -or -not $compose) {
    Docker-Screen (Say 'Docker Compose v2 is missing' 'Docker Compose v2가 없습니다') @(
      (Say 'Update Docker Desktop; it includes Compose:' 'Docker Desktop을 업데이트하세요. Compose가 포함되어 있습니다:'), '  https://docs.docker.com/desktop/release-notes/')
  }
  Ok "Docker Compose $compose"
  & docker info *> $null
  if ($LASTEXITCODE -ne 0) {
    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if ((Test-Path $exe) -and (Ask-YesNo (Say 'Docker Desktop is not running. Open it now?' 'Docker Desktop이 실행 중이 아닙니다. 지금 열까요?') $true)) {
      Start-Process $exe
      Step (Say 'Waiting for Docker Desktop to start' 'Docker Desktop이 시작되기를 기다리는 중') {
        for ($k = 0; $k -lt 90; $k++) { docker info *> $null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 2 }
      }
    } else {
      Docker-Screen (Say 'Docker is not running' 'Docker가 실행 중이 아닙니다') @(
        (Say 'Open Docker Desktop and wait until it says it is running.' 'Docker Desktop을 열고 실행 중이라고 나올 때까지 기다려 주세요.'))
    }
  }
  Ok (Say 'Docker is running' 'Docker 실행 중')
}

# ------------------------------------------------------------------------------------------------------------- uv
function Find-Uv {
  $c = Get-Command uv -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($p in @((Join-Path $env:USERPROFILE '.local\bin\uv.exe'), (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'))) { if (Test-Path $p) { return $p } }
  return $null
}

function Ensure-Uv {
  $script:Uv = Find-Uv
  if (-not $script:Uv) {
    Note (Say "uv (Astral's Python tool) is needed to run the setup; installing it for this user only." '설정을 실행하려면 uv(Astral의 Python 도구)가 필요합니다. 이 사용자에게만 설치합니다.')
    Step (Say 'Installing uv' 'uv 설치 중') {
      $env:UV_NO_MODIFY_PATH = '1'
      Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
    $script:Uv = Find-Uv
    if (-not $script:Uv) { Die (Say 'uv was installed but cannot be found. Open a new PowerShell and run this again.' 'uv를 설치했지만 찾을 수 없습니다. 새 PowerShell을 열고 다시 실행하세요.') }
  }
  Ok ("uv " + ((& $script:Uv --version) -split ' ')[1])
}

# ------------------------------------------------------------------------------------------------------------- Kelo files
# Never overwritten by an update: settings, keys, extra copies, address credentials, the phone app projects.
$KeepDirs = @('.venv', '.git', 'instances', 'connect', 'android', 'ios', 'node_modules', 'dist', '__pycache__', '.pytest_cache', '.ruff_cache')
$KeepFiles = @('.env', '.env.*', '*.log')

function Copy-Tree([string]$from, [string]$to) {
  $f = (Resolve-Path $from).Path.TrimEnd('\'); New-Item -ItemType Directory -Force -Path $to | Out-Null; $t = (Resolve-Path $to).Path.TrimEnd('\')
  if ($f -eq $t) { return }
  & robocopy $f $t /E /NFL /NDL /NJH /NJS /NP /XD @KeepDirs /XF @KeepFiles | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
  $global:LASTEXITCODE = 0
}

# Where the Kelo files come from: KELO_SOURCE (a local folder, a .zip / .tar.gz URL or a git URL), else the public source's newest
# release (github.com/kelo-chat/kelo-server: the server part of each release; its main branch is the newest release).
$DefaultSource = "$ReleasesBase/archive/refs/heads/main.zip"
function Get-VersionSource { "$ReleasesBase/archive/refs/tags/release-$Version.zip" }

# The "version" in a version.json, or $null.
function Read-JsonVersion([string]$file) {
  if (-not (Test-Path $file)) { return $null }
  try { return [string]((Get-Content -Raw $file | ConvertFrom-Json).version) } catch { return $null }
}

# With -Version, the files about to be copied must be that release (a custom -Source too): checked before anything is copied.
function Test-FilesVersion([string]$top, [string]$stage) {
  if (-not $Version) { return }
  $got = Read-JsonVersion (Join-Path $top 'version.json')
  if (-not $got -or $got -eq $Version) { return }
  if ($stage -and (Test-Path $stage)) { Remove-Item -Recurse -Force $stage }
  Die (Say "These files are Kelo $got, not $Version. Nothing was changed." "이 파일은 Kelo $got 이고 $Version 이 아닙니다. 아무것도 바꾸지 않았습니다.")
}

function Fetch-Kelo {
  if (-not $Source -and $Version) {
    $script:Source = Get-VersionSource
  } elseif (-not $Source) {
    if (Test-Path (Join-Path $Dir 'compose.yml')) { Note (Say "No KELO_SOURCE given: keeping the Kelo files already in $Dir." "KELO_SOURCE가 없어 $Dir 에 있는 Kelo 파일을 그대로 사용합니다."); return }
    $script:Source = $DefaultSource
  }
  New-Item -ItemType Directory -Force -Path $Dir | Out-Null
  $stage = Join-Path $env:TEMP ("kelo-src-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
  if ($Source -match '\.git$' -or $Source -like 'git@*') {
    Step (Say 'Downloading Kelo' 'Kelo 내려받는 중') ([scriptblock]::Create("git clone --depth 1 '$Source' '$stage'"))
    $top = $stage
    Test-FilesVersion $top $stage
  } elseif ($Source -match '^https?://') {
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    $archive = if ($Source -match '\.zip($|\?)') { Join-Path $stage 'kelo.zip' } else { Join-Path $stage 'kelo.tar.gz' }
    Step (Say 'Downloading Kelo' 'Kelo 내려받는 중') ([scriptblock]::Create("Invoke-WebRequest -UseBasicParsing -Uri '$Source' -OutFile '$archive'"))
    $x = Join-Path $stage 'x'; New-Item -ItemType Directory -Force -Path $x | Out-Null
    if ($archive -like '*.zip') { Expand-Archive -Path $archive -DestinationPath $x -Force } else { & tar -xzf $archive -C $x }
    $items = @(Get-ChildItem $x)
    $top = if ($items.Count -eq 1 -and $items[0].PSIsContainer) { $items[0].FullName } else { $x }
  } else {
    $top = $Source
  }
  if (-not (Test-Path (Join-Path $top 'compose.yml'))) { Die (Say "$Source does not look like Kelo (no compose.yml)." "$Source 는 Kelo가 아닌 것 같습니다 (compose.yml 없음).") }
  Test-FilesVersion $top $(if ($top -ne $Source) { $stage } else { '' })
  Note (Say "Putting the files in $Dir" "$Dir 에 파일 넣는 중")
  Copy-Tree $top $Dir
  Ok (Say "Kelo files are in $Dir" "Kelo 파일을 $Dir 에 넣었습니다")
  if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
}

function Install-Launcher {
  New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
  $cmd = Join-Path $BinDir 'kelo.cmd'
  @"
@echo off
rem Kelo's command (made by the installer): runs ``assistant`` in $Dir. Example: kelo status, kelo phone add
pushd "$Dir" || exit /b 1
set KELO_CMD=kelo
"$Uv" run --quiet --no-dev --frozen assistant %*
set rc=%ERRORLEVEL%
popd
exit /b %rc%
"@ | Set-Content -Path $cmd -Encoding ASCII
  Ok (Say "Added the kelo command ($cmd)" "kelo 명령을 추가했습니다 ($cmd)")
  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if (($userPath -split ';') -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $BinDir), 'User')
    Note (Say "Added $BinDir to your PATH (new terminals will find kelo)." "$BinDir 를 PATH에 추가했습니다 (새 터미널부터 kelo를 쓸 수 있습니다).")
  }
}

# ------------------------------------------------------------------------------------------------------------- -Version
# Compare-Version A B: -1, 0 or 1. X.Y.Z compares number by number; a pre-release (0.5.0-rc.1) is older than its release;
# a +build suffix is ignored.
function Compare-Version([string]$a, [string]$b) {
  $a = ($a -replace '^v', '') -replace '\+.*$', ''; $b = ($b -replace '^v', '') -replace '\+.*$', ''
  $pa = ''; $pb = ''
  if ($a -match '^([^-]*)-(.+)$') { $a = $Matches[1]; $pa = $Matches[2] }
  if ($b -match '^([^-]*)-(.+)$') { $b = $Matches[1]; $pb = $Matches[2] }
  $av = $a.Split('.'); $bv = $b.Split('.')
  for ($i = 0; $i -lt 3; $i++) {
    $x = [bigint]0; $y = [bigint]0
    if ($i -lt $av.Count) { [void][bigint]::TryParse($av[$i], [ref]$x) }
    if ($i -lt $bv.Count) { [void][bigint]::TryParse($bv[$i], [ref]$y) }
    if ($x -lt $y) { return -1 }
    if ($x -gt $y) { return 1 }
  }
  if (-not $pa -and -not $pb) { return 0 }
  if (-not $pa) { return 1 }
  if (-not $pb) { return -1 }
  $ap = $pa.Split('.'); $bp = $pb.Split('.')
  for ($i = 0; $i -lt [Math]::Max($ap.Count, $bp.Count); $i++) {
    if ($i -ge $ap.Count) { return -1 }
    if ($i -ge $bp.Count) { return 1 }
    $x = $ap[$i]; $y = $bp[$i]
    $xn = $x -match '^\d+$'; $yn = $y -match '^\d+$'
    if ($xn -and $yn) {
      if ([bigint]$x -lt [bigint]$y) { return -1 }
      if ([bigint]$x -gt [bigint]$y) { return 1 }
    } elseif ($xn) { return -1 }
    elseif ($yn) { return 1 }
    else {
      $c = [string]::CompareOrdinal($x, $y)
      if ($c -lt 0) { return -1 }
      if ($c -gt 0) { return 1 }
    }
  }
  return 0
}

# Checks -Version before anything is changed: its form, that it is not older than what is installed (unless -AllowDowngrade), and,
# for the official source, that the release exists. Each failure is one line and stops the installer.
function Test-Version {
  if (-not $Version) { return }
  $script:Version = $Version -replace '^v', ''
  if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]{1,30})?$') {
    Die (Say "'$Version' is not a release number (example: -Version 0.10.2). Versions: $ReleasesBase/releases" "'$Version' 은 릴리스 번호가 아닙니다 (예: -Version 0.10.2). 버전 목록: $ReleasesBase/releases")
  }
  $have = Read-JsonVersion (Join-Path $Dir 'version.json')
  if ((Test-Path (Join-Path $Dir 'compose.yml')) -and $have -and (Compare-Version $Version $have) -lt 0 -and -not $AllowDowngrade) {
    Die (Say "Kelo $have is installed; $Version is older and data may not go back. Nothing was changed. To go back anyway add -AllowDowngrade." "Kelo $have 이(가) 설치되어 있고 $Version 은 더 오래된 버전이라 데이터가 되돌아가지 않을 수 있습니다. 아무것도 바꾸지 않았습니다. 그래도 되돌리려면 -AllowDowngrade 를 붙이세요.")
  }
  if ($Source) { return }     # a source of your own is checked by its version.json when it is copied
  $code = 0
  try { $code = [int](Invoke-WebRequest -UseBasicParsing -Method Head -Uri (Get-VersionSource)).StatusCode }
  catch {
    $response = $_.Exception.PSObject.Properties['Response']
    if ($response -and $response.Value) { $code = [int]$response.Value.StatusCode }
  }
  if ($code -eq 404) {
    Die (Say "Kelo version $Version was not found. Nothing was changed. All versions: $ReleasesBase/releases" "Kelo $Version 버전을 찾을 수 없습니다. 아무것도 바꾸지 않았습니다. 전체 버전 목록: $ReleasesBase/releases")
  }
}

# Does the Kelo code in $Dir know this option? (a release from before an option cannot be asked to use it)
function Test-CodeKnows([string]$option, [string[]]$command) {
  $help = (& $Uv run --quiet --no-dev --frozen assistant @command --help 2>$null) -join "`n"
  return [bool]($help -match [regex]::Escape($option))
}

# ------------------------------------------------------------------------------------------------------------- uninstall
function Remove-Kelo {
  Show-Banner
  Section (Say 'Remove Kelo' 'Kelo 제거')
  if (-not (Test-Path (Join-Path $Dir 'compose.yml'))) { Die (Say "No Kelo in $Dir (use -Dir to point at it)." "$Dir 에 Kelo가 없습니다 (-Dir로 위치를 지정하세요).") }
  if ($DeleteData) { Warn (Say '-DeleteData: every chat, key, file and phone sign-in of every copy is deleted for good.' '-DeleteData: 모든 복사본의 대화, 키, 파일, 휴대폰 연결이 영구히 삭제됩니다.') }
  else { Note (Say "Your data stays (Docker volumes and $Dir\.env). Installing again brings everything back." "데이터는 남겨 둡니다 (Docker 볼륨과 $Dir\.env). 다시 설치하면 모두 돌아옵니다.") }
  if (-not $Yes) {
    if ([Console]::IsInputRedirected) { Die (Say 'Removing needs a terminal to confirm, or -Yes.' '제거하려면 터미널에서 확인하거나 -Yes가 필요합니다.') }
    if (-not (Ask-YesNo (Say 'Remove Kelo from this server?' '이 서버에서 Kelo를 제거할까요?') $false)) { Note (Say 'Nothing was changed.' '아무것도 바꾸지 않았습니다.'); Stop-Install 0 }
  }
  Set-Location $Dir
  $script:Uv = Find-Uv
  if ($script:Uv) {
    foreach ($d in @(Get-ChildItem -Directory (Join-Path $Dir 'instances') -ErrorAction SilentlyContinue)) {
      if (-not (Test-Path (Join-Path $d.FullName 'env'))) { continue }
      if ($DeleteData) {
        Step ((Say 'Deleting the copy ' '복사본 삭제 중: ') + $d.Name) ([scriptblock]::Create("& '$($script:Uv)' run --quiet --no-dev --frozen assistant instance delete $($d.Name) --yes"))
      } else {
        Step ((Say 'Stopping the copy (its data stays) ' '복사본 멈추는 중 (데이터는 남김): ') + $d.Name) ([scriptblock]::Create("& '$($script:Uv)' run --quiet --no-dev --frozen assistant instance stop $($d.Name)"))
      }
    }
    if (Test-Path (Join-Path $Dir '.env')) { & $script:Uv run --quiet --no-dev --frozen assistant connect off *>> $Log }
  }
  if ($DeleteData) {
    Step (Say 'Stopping Kelo and deleting its data' 'Kelo를 멈추고 데이터 삭제 중') { docker compose --profile '*' down --volumes --remove-orphans }
    Set-Location $env:USERPROFILE
    Remove-Item -Recurse -Force $Dir
    Ok (Say "Deleted $Dir" "$Dir 삭제함")
  } else {
    Step (Say 'Stopping and removing the containers' '컨테이너를 멈추고 제거하는 중') { docker compose --profile '*' down --remove-orphans }
  }
  Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $BinDir 'kelo.cmd')
  Ok (Say 'Kelo is removed. Docker Desktop and uv stay installed.' 'Kelo를 제거했습니다. Docker Desktop과 uv는 그대로 둡니다.')
}

# ------------------------------------------------------------------------------------------------------------- updates from the app
# The updater container (compose profile `updater`): the owner presses Update in the app, or lets it update at night. Its secret
# (UPDATER_TOKEN) is made by `assistant updater setup` and never shown. The images are the signed official ones (checked by
# `assistant`, digest and signature) or were built from these files; the app says to update with `kelo update` in the second case.
$script:AutoOn = [bool]$AutoUpdate   # decided by Ask-AutoUpdate when there is a terminal; the default otherwise

function Ask-AutoUpdate {
  Section (Say 'Updates from the app' '앱에서 하는 업데이트')
  if ($AutoUpdate) { $script:AutoOn = $true; return }
  if ($NoAutoUpdate) { $script:AutoOn = $false; return }
  Note (Say "The owner's phone can update this server with one button (My → Server update). Kelo can also do it by itself at night," '주인 휴대폰에서 버튼 하나로 이 서버를 업데이트할 수 있습니다 (마이 → 서버 업데이트). 밤에 Kelo가 알아서 할 수도 있습니다,')
  Note (Say 'when you usually do not use it; a backup is made first and it goes back by itself if the new version fails.' '평소 쓰지 않는 시간에요. 먼저 백업하고, 새 버전이 실패하면 저절로 되돌립니다.')
  $script:AutoOn = Ask-YesNo (Say 'Update automatically at night?' '밤에 자동으로 업데이트할까요?') $false
}

# assistant updater setup with this run's choices plus the extra arguments; -Soft: a failure returns (see Step)
function Run-UpdaterSetup([string]$label, [string[]]$Extra = @(), [switch]$Soft) {
  $u = @('updater', 'setup', $(if ($script:AutoOn) { '--auto-update' } else { '--no-auto-update' }))
  if ($env:KELO_REGISTRY) { $u += @('--registry', $env:KELO_REGISTRY) }
  if ($NoStart) { $u += '--no-start' }
  $u += $Extra
  $cmd = "& '$Uv' run --quiet --no-dev --frozen assistant " + (($u | ForEach-Object { "'$_'" }) -join ' ')
  Step $label ([scriptblock]::Create($cmd)) -Soft:$Soft
}

function Setup-Updater {
  Ask-AutoUpdate
  Run-UpdaterSetup (Say 'Setting up the updater' '업데이트 도구 설정 중')
  if ($script:AutoOn) { Ok (Say 'Nightly auto-update is on (change it in the app: My → Server update)' '밤 자동 업데이트를 켰습니다 (앱의 마이 → 서버 업데이트에서 바꿀 수 있습니다)') }
  else { Ok (Say 'Updates when the owner presses Update in the app (My → Server update)' '앱에서 업데이트를 누를 때 업데이트합니다 (마이 → 서버 업데이트)') }
}

# Without a terminal, the first install pulls the official images before Kelo starts (the updater's settings come with the pull).
# If the pull fails, the images are built here instead, as before, with a warning.
function Pull-FirstInstall {
  $pullArgs = @('--no-start', '--pull-release'); $try = $true
  if ($Version) {
    if (Test-CodeKnows '--release-version' @('updater', 'setup')) { $pullArgs += @('--release-version', $Version) }
    else {
      $try = $false
      Warn (Say "Kelo $Version cannot pull its own signed images by version; building them here from its files instead." "Kelo $Version 은 자기 서명 이미지를 버전으로 내려받을 수 없어, 그 파일로 이 PC에서 직접 빌드합니다.")
    }
  }
  if ($try) {
    Run-UpdaterSetup (Say 'Downloading the official Kelo images and checking their signatures' '공식 Kelo 이미지를 내려받고 서명을 확인하는 중') -Extra $pullArgs -Soft
    if ($script:StepCode -eq 0) {
      if (-not $NoStart) { Step (Say 'Starting Kelo' 'Kelo 시작 중') { $env:BUILDKIT_PROGRESS = 'plain'; docker compose up -d --remove-orphans } }
      return
    }
    $why = @(Get-Content $Log | Where-Object { $_ -like 'error:*' }) | Select-Object -Last 1
    $why = if ($why) { ($why -replace '^error:\s*', '') } else { '' }
    $head = if ($why) { ": $why" } else { '' }
    Warn (Say "Could not get the official images$head. Building them here instead." "공식 이미지를 받지 못했습니다$head. 이 PC에서 직접 빌드합니다.")
  }
  if (-not $NoStart) {
    Step (Say 'Building and starting Kelo (the first time takes a few minutes)' 'Kelo를 빌드하고 시작하는 중 (처음에는 몇 분 걸립니다)') { $env:BUILDKIT_PROGRESS = 'plain'; docker compose up -d --build --remove-orphans }
  }
}

# ------------------------------------------------------------------------------------------------------------- main
function Invoke-KeloInstall {
  if ($Uninstall) { Remove-Kelo; return }
  Show-Banner
  Test-Platform
  Test-Version
  $mode = if (Test-Path (Join-Path $Dir 'compose.yml')) { Say 'update (data kept)' '업데이트 (데이터 유지)' } else { Say 'new install' '새로 설치' }
  Write-Host ''
  Note ("{0,-12} {1}" -f (Say 'server' '서버'), "$Platform · $Arch")
  Note ("{0,-12} {1}" -f (Say 'folder' '폴더'), $Dir)
  Note ("{0,-12} {1}" -f (Say 'what' '작업'), $mode)
  if ($Version) { Note ("{0,-12} {1}" -f (Say 'version' '버전'), $Version) }
  if ($Source) { Note ("{0,-12} {1}" -f (Say 'from' '가져올 곳'), $Source) }

  Section 'Docker'
  Test-Docker
  Section (Say 'Tools' '도구')
  Ensure-Uv
  Section (Say 'Kelo files' 'Kelo 파일')
  Fetch-Kelo
  Set-Location $Dir
  Step (Say "Preparing Python and Kelo's packages" 'Python과 Kelo 패키지 준비 중') ([scriptblock]::Create("& '$Uv' sync --frozen --no-dev"))
  Install-Launcher

  $env:KELO_CMD = 'kelo'
  if ($Interactive) {
    Write-Host ''
    # Kelo files from before 0.10.2 have no `setup --from-release`: their guided screen saves the answers only, and the images
    # are pulled (or built) after it, as before (no QR step then: the closing note says how to connect a phone).
    $wizardPulls = $false
    if ($FromRelease) {
      $help = (& $Uv run --quiet --no-dev --frozen assistant setup --help 2>$null) -join "`n"
      $wizardPulls = $help -match '--from-release'
      if ($wizardPulls -and $Version) { $wizardPulls = Test-CodeKnows '--release-version' @('setup') }
    }
    $setupArgs = @('run', '--quiet', '--no-dev', '--frozen', 'assistant', 'setup')
    if ($NoStart -or ($FromRelease -and -not $wizardPulls)) { $setupArgs += '--no-start' }
    if ($wizardPulls) { $setupArgs += '--from-release' }   # the guided screen pulls the official images, then starts Kelo
    if ($wizardPulls -and $Version) { $setupArgs += @('--release-version', $Version) }
    & $Uv @setupArgs
    if ($LASTEXITCODE) { Stop-Install $LASTEXITCODE }
    if ($FromRelease -and -not $wizardPulls) {
      Pull-FirstInstall
      Note (Say 'Next: kelo connect kelo --name <name>   then   kelo phone add' '다음: kelo connect kelo --name <이름>   그리고   kelo phone add')
    }
    Setup-Updater
    return
  }
  Section (Say 'Setup (no questions)' '설정 (질문 없이)')
  & $Uv run --quiet --no-dev --frozen assistant setup --yes *>> $Log
  if ($LASTEXITCODE -ne 0) { Die (Say "Setup failed. Log: $Log" "설정이 실패했습니다. 로그: $Log") }
  Ok (Say "Saved the settings in $Dir\.env" "$Dir\.env 에 설정을 저장했습니다")
  if ($FromRelease) { Pull-FirstInstall }
  elseif (-not $NoStart) {
    Step (Say 'Building and starting Kelo (the first time takes a few minutes)' 'Kelo를 빌드하고 시작하는 중 (처음에는 몇 분 걸립니다)') {
      $env:BUILDKIT_PROGRESS = 'plain'; docker compose up -d --build --remove-orphans
    }
  }
  Setup-Updater
  Note (Say 'Next: kelo connect kelo --name <name>   then   kelo phone add' '다음: kelo connect kelo --name <이름>   그리고   kelo phone add')
  Remove-Item -Force -ErrorAction SilentlyContinue $Log
}

$global:LASTEXITCODE = 0
try { Invoke-KeloInstall }
catch [KeloStop] { $global:LASTEXITCODE = $_.Exception.Code }
