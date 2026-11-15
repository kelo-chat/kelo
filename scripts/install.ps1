<#
  Kelo installer for Windows (Docker Desktop with WSL 2). macOS and Linux: scripts/install.sh

    irm <address to be decided>/install.ps1 | iex                       install, or update when Kelo is already there
    & ([scriptblock]::Create((irm <address>/install.ps1))) -Yes         no questions (defaults everywhere)
    .\install.ps1 -Uninstall                                             remove Kelo (your data is kept unless -DeleteData)

  What it does, in order: checks this PC (Windows version, CPU type, WSL 2), checks Docker Desktop and Compose (and says how to
  get them), installs `uv` if it is missing, puts the Kelo files in %USERPROFILE%\kelo, prepares Python with uv, adds a `kelo`
  command, and opens the guided setup. Running it again is an update: .env, chats, keys and phones stay as they are.

  Parameters (each also as an environment variable):
    -Yes          answer every question with its default (KELO_YES=1); never deletes data
    -Dir DIR      where Kelo lives (KELO_HOME, default %USERPROFILE%\kelo)
    -Source SRC   where to get the Kelo files: a folder, a .zip / .tar.gz URL or a git URL (KELO_SOURCE)
    -NoStart      save the answers but do not build or start the containers (KELO_NO_START=1)
    -FromRelease  pull the signed release images instead of building them here (KELO_FROM_RELEASE=1); the Claude/agy
                  containers are still built here (their CLIs are never published)
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
  [switch]$FromRelease,
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
if ($env:KELO_YES -eq '1') { $Yes = $true }
if ($env:KELO_NO_START -eq '1') { $NoStart = $true }
if ($env:KELO_FROM_RELEASE -eq '1') { $FromRelease = $true }
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

# Step "what is happening" { command }: one line with a spinner; output goes to the log, shown only if it fails.
function Step([string]$label, [scriptblock]$block) {
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
  if ($code -eq 0) { Ok "$label ($secs s)"; return }
  W '  ✗ ' 'Red'; Write-Host $label
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

# Placeholder until the public download location is decided: KELO_SOURCE may be a local folder, a .zip / .tar.gz URL or a git URL.
function Fetch-Kelo {
  if (-not $Source) {
    if (Test-Path (Join-Path $Dir 'compose.yml')) { Note (Say "No KELO_SOURCE given: keeping the Kelo files already in $Dir." "KELO_SOURCE가 없어 $Dir 에 있는 Kelo 파일을 그대로 사용합니다."); return }
    Die (Say "Where to download Kelo from is not decided yet. For now set KELO_SOURCE to a Kelo folder, a .zip URL or a git URL:`n      `$env:KELO_SOURCE='C:\path\to\assistant-server'; .\install.ps1" `
             "Kelo 다운로드 위치가 아직 정해지지 않았습니다. 지금은 KELO_SOURCE에 Kelo 폴더, .zip 주소 또는 git 주소를 지정하세요:`n      `$env:KELO_SOURCE='C:\path\to\assistant-server'; .\install.ps1")
  }
  New-Item -ItemType Directory -Force -Path $Dir | Out-Null
  $stage = Join-Path $env:TEMP ("kelo-src-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
  if ($Source -match '\.git$' -or $Source -like 'git@*') {
    Step (Say 'Downloading Kelo' 'Kelo 내려받는 중') ([scriptblock]::Create("git clone --depth 1 '$Source' '$stage'"))
    $top = $stage
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
# (UPDATER_TOKEN) is made by `assistant updater setup` and never shown. -FromRelease pulls and checks the signed images first.
function Setup-Updater {
  Section (Say 'Updates from the app' '앱에서 하는 업데이트')
  $auto = $false
  if ($AutoUpdate) { $auto = $true }
  elseif (-not $NoAutoUpdate) {
    Note (Say "The owner's phone can update this server with one button (My → Server update). Kelo can also do it by itself at night," '주인 휴대폰에서 버튼 하나로 이 서버를 업데이트할 수 있습니다 (마이 → 서버 업데이트). 밤에 Kelo가 알아서 할 수도 있습니다,')
    Note (Say 'when you usually do not use it; a backup is made first and it goes back by itself if the new version fails.' '평소 쓰지 않는 시간에요. 먼저 백업하고, 새 버전이 실패하면 저절로 되돌립니다.')
    $auto = Ask-YesNo (Say 'Update automatically at night?' '밤에 자동으로 업데이트할까요?') $false
  }
  $u = @('updater', 'setup', $(if ($auto) { '--auto-update' } else { '--no-auto-update' }))
  if ($env:KELO_REGISTRY) { $u += @('--registry', $env:KELO_REGISTRY) }
  if ($NoStart -or $FromRelease) { $u += '--no-start' }
  if ($FromRelease) { $u += '--pull-release' }
  $cmd = "& '$Uv' run --quiet --no-dev --frozen assistant " + (($u | ForEach-Object { "'$_'" }) -join ' ')
  $label = if ($FromRelease) { Say 'Downloading the signed Kelo images and checking them' '서명된 Kelo 이미지를 내려받고 확인하는 중' } else { Say 'Setting up the updater' '업데이트 도구 설정 중' }
  Step $label ([scriptblock]::Create($cmd))
  if ($FromRelease -and -not $NoStart) {
    Step (Say 'Starting Kelo' 'Kelo 시작 중') { $env:BUILDKIT_PROGRESS = 'plain'; docker compose up -d --remove-orphans }
  }
  if ($auto) { Ok (Say 'Nightly auto-update is on (change it in the app: My → Server update)' '밤 자동 업데이트를 켰습니다 (앱의 마이 → 서버 업데이트에서 바꿀 수 있습니다)') }
  else { Ok (Say 'Updates when the owner presses Update in the app (My → Server update)' '앱에서 업데이트를 누를 때 업데이트합니다 (마이 → 서버 업데이트)') }
}

# ------------------------------------------------------------------------------------------------------------- main
function Invoke-KeloInstall {
  if ($Uninstall) { Remove-Kelo; return }
  Show-Banner
  Test-Platform
  $mode = if (Test-Path (Join-Path $Dir 'compose.yml')) { Say 'update (data kept)' '업데이트 (데이터 유지)' } else { Say 'new install' '새로 설치' }
  Write-Host ''
  Note ("{0,-12} {1}" -f (Say 'server' '서버'), "$Platform · $Arch")
  Note ("{0,-12} {1}" -f (Say 'folder' '폴더'), $Dir)
  Note ("{0,-12} {1}" -f (Say 'what' '작업'), $mode)
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
    $setupArgs = @('run', '--quiet', '--no-dev', '--frozen', 'assistant', 'setup')
    if ($NoStart -or $FromRelease) { $setupArgs += '--no-start' }   # release images are pulled in Setup-Updater, never built here
    & $Uv @setupArgs
    if ($LASTEXITCODE) { Stop-Install $LASTEXITCODE }
    Setup-Updater
    return
  }
  Section (Say 'Setup (no questions)' '설정 (질문 없이)')
  & $Uv run --quiet --no-dev --frozen assistant setup --yes *>> $Log
  if ($LASTEXITCODE -ne 0) { Die (Say "Setup failed. Log: $Log" "설정이 실패했습니다. 로그: $Log") }
  Ok (Say "Saved the settings in $Dir\.env" "$Dir\.env 에 설정을 저장했습니다")
  if (-not $NoStart -and -not $FromRelease) {
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
