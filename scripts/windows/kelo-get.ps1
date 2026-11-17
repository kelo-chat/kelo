<#
  Kelo Server for Windows: one line, no questions in the terminal.

    irm https://kelo.chat/install-windows.ps1 | iex

  What happens: the PC is checked (64-bit, Windows 10 version 2004 or later, or Windows 11). Windows asks for administrator
  permission once (click Yes). The Kelo Server installer (an MSI) is downloaded from kelo.chat and its size and SHA-256 are
  checked against the release list (latest.json, row "windows-server-msi"; the row is not published yet, so the script stops
  before anything changes). The installer is run silently with msiexec. Kelo's own steps
  (WSL, the server, the background task) run by themselves, and the setup page opens in the browser: there you choose the
  size, the address and the phone pairing. Running the same line again installs the newest version over the old one; your
  data stays.

  Nothing here asks a question. Progress and the result are printed; the log is in %ProgramData%\Kelo\logs.
  Docker Desktop users: the Docker installer is install.ps1 (irm https://kelo.chat/install.ps1 | iex).

  Test settings (optional, each also a parameter-less environment variable): KELO_GET_BASE (default https://kelo.chat),
  KELO_LANG (ko or en; default: the Windows display language).
#>
[CmdletBinding()]
param(
  [switch]$Elevated,   # set by the relaunch below: the administrator copy of this same script
  [string]$Stamp       # the log name stamp, shared by the relaunch and its child
)

function Invoke-KeloServerInstall {
  param([bool]$Elevated, [string]$Stamp)
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  $Base = if ($env:KELO_GET_BASE) { $env:KELO_GET_BASE.TrimEnd('/') } else { 'https://kelo.chat' }
  $Ko = (Get-UICulture).Name -like 'ko*'
  if ($env:KELO_LANG) { $Ko = $env:KELO_LANG -like 'ko*' }
  function Say([string]$en, [string]$ko) { if ($Ko) { $ko } else { $en } }
  function Step([string]$t) { Write-Host "  $t" }
  function Get-KeloVersion {
    $keys = @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
    Get-ItemProperty -Path $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -eq 'Kelo Server' } |
      Select-Object -First 1 -ExpandProperty DisplayVersion
  }

  $logDir = Join-Path $env:ProgramData 'Kelo\logs'
  if (-not $Stamp) { $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss' }
  $resultFile = Join-Path $logDir "get-$Stamp.result.txt"
  $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

  Write-Host ''
  Write-Host (Say 'Kelo Server installer' 'Kelo Server 설치')
  Step (Say 'Checking this PC' '이 PC 확인 중')
  $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
  $build = [int](Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name CurrentBuildNumber).CurrentBuildNumber
  if ($arch -ne 'AMD64') {
    throw (Say 'Kelo Server needs a 64-bit Windows PC. Nothing was changed.' '64비트 Windows PC가 필요합니다. 아무것도 바뀌지 않았습니다.')
  }
  if ($build -lt 19041) {
    throw (Say 'Kelo Server needs Windows 10 version 2004 or later, or Windows 11. Update Windows first; nothing was changed.' 'Windows 10 2004 이후 버전 또는 Windows 11이 필요합니다. Windows를 먼저 업데이트하세요. 아무것도 바뀌지 않았습니다.')
  }

  # 0. The release list first: no prompt and no change when the server installer is not published
  $releases = Invoke-RestMethod -UseBasicParsing -Uri "$Base/download/latest.json"
  $row = $releases.'windows-server-msi'
  if (-not $row -or -not $row.url -or -not $row.sha256 -or -not $row.size -or -not $row.version) {
    throw (Say 'Kelo Server for Windows is not released yet. Nothing was changed.' 'Kelo Server Windows 버전은 아직 공개되지 않았습니다. 아무것도 바뀌지 않았습니다.')
  }
  if (([string]$row.url) -notmatch '^/[\w/.-]+$') { throw 'the release list row has an unexpected address' }

  # 1. Not an administrator yet: run this same script elevated (the one click is Windows' own permission prompt)
  if (-not $Elevated -and -not $isAdmin) {
    Step (Say 'Windows will ask for administrator permission. Click Yes.' 'Windows가 관리자 권한을 묻습니다. 예를 누르세요.')
    if ($PSCommandPath) {
      $argText = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated -Stamp $Stamp"
    } else {
      $argText = "-NoProfile -ExecutionPolicy Bypass -Command `"& ([scriptblock]::Create((Invoke-RestMethod -UseBasicParsing -Uri '$Base/install-windows.ps1'))) -Elevated -Stamp $Stamp`""
    }
    try {
      $child = Start-Process -FilePath 'powershell.exe' -ArgumentList $argText -Verb RunAs -PassThru -Wait
    } catch {
      throw (Say 'Administrator permission was not given, so nothing was installed. Paste the same line again and click Yes.' '관리자 권한이 허용되지 않아 아무것도 설치하지 않았습니다. 같은 줄을 다시 붙여 넣고 예를 누르세요.')
    }
    $res = @{}
    if (Test-Path -LiteralPath $resultFile) {
      Get-Content -LiteralPath $resultFile -Encoding UTF8 | ForEach-Object { if ($_ -match '^(\w+)=(.*)$') { $res[$matches[1]] = $matches[2] } }
    }
    if (-not $res.message) {
      throw (Say "The installer stopped before it finished (exit $($child.ExitCode)). The log folder: $logDir" "설치가 끝나기 전에 멈췄습니다(종료 코드 $($child.ExitCode)). 로그 폴더: $logDir")
    }
    Write-Host ''
    Write-Host "  $($res.message)"
    # the browser is opened here, in the user's own session (the elevated copy may not reach the user's desktop)
    if ($res.status -eq 'ok' -and $res.opened -ne '1' -and $res.url) {
      try { Start-Process $res.url } catch { }
    }
    return
  }

  # 2. Administrator: fetch the release list and the installer row
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $log = Join-Path $logDir "get-$Stamp.log"
  function Write-KeloResult([string]$status, [string]$message, [string]$version, [string]$url, [bool]$opened) {
    $lines = @("status=$status", "version=$version", "url=$url", "opened=$(if ($opened) { 1 } else { 0 })", "message=$message")
    Set-Content -LiteralPath $resultFile -Value $lines -Encoding UTF8
  }
  $before = Get-KeloVersion
  $version = [string]$row.version

  # 3. Download, check size and SHA-256, install
  $msi = Join-Path $env:TEMP ("kelo-$version.msi")
  try {
    Step (Say "Downloading Kelo Server $version ($([math]::Round([int64]$row.size / 1MB)) MB)" "Kelo Server $version 내려받는 중 (약 $([math]::Round([int64]$row.size / 1MB)) MB)")
    Invoke-WebRequest -UseBasicParsing -Uri ($Base + [string]$row.url) -OutFile $msi
    Step (Say 'Checking the download' '내려받은 파일 확인 중')
    if ((Get-Item -LiteralPath $msi).Length -ne [int64]$row.size) {
      throw (Say 'The download has the wrong size, so it was not installed. Try again later.' '내려받은 파일 크기가 맞지 않아 설치하지 않았습니다. 나중에 다시 시도하세요.')
    }
    $hash = (Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash
    if ($hash -ine [string]$row.sha256) {
      throw (Say 'The download does not match the release list, so it was not installed. Try again later.' '내려받은 파일이 배포 목록과 맞지 않아 설치하지 않았습니다. 나중에 다시 시도하세요.')
    }

    Step (Say 'Installing. This takes a few minutes; there is nothing to do.' '설치 중입니다. 몇 분 걸립니다. 할 일은 없습니다.')
    $p = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i', "`"$msi`"", '/qn', '/norestart', '/l*v', "`"$log`"") -Wait -PassThru
    if ($p.ExitCode -eq 3010 -or $p.ExitCode -eq 1641) {
      $msg = Say 'Restart Windows. Kelo finishes the setup by itself after you sign in.' 'Windows를 다시 시작하세요. 다시 로그인하면 Kelo가 설정을 알아서 마칩니다.'
      Write-KeloResult 'reboot' $msg $version '' $false
      Write-Host "  $msg"
      return
    }
    if ($p.ExitCode -ne 0) {
      throw (Say "The installer stopped with code $($p.ExitCode). Nothing else was changed. The log: $log" "설치 프로그램이 코드 $($p.ExitCode)로 멈췄습니다. 다른 것은 바꾸지 않았습니다. 로그: $log")
    }
  } finally {
    Remove-Item -LiteralPath $msi -Force -ErrorAction SilentlyContinue
  }

  # 4. The setup page: the Windows part of the installer opens it in the user's browser; if it could not, this opens it
  $after = Get-KeloVersion
  $url = 'http://localhost:8080/'
  $opened = $false
  $userLog = Get-ChildItem -Path $logDir -Filter 'install-user-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($userLog) {
    $text = Get-Content -LiteralPath $userLog.FullName -Raw -ErrorAction SilentlyContinue
    if ($text -match 'Opening the setup page: (https?://\S+)') {
      $url = $matches[1]
      $opened = $text -notmatch 'Could not open a browser'
    }
  }
  if ($before -and $before -eq $after) { $head = Say "Kelo Server $after is already the newest version." "Kelo Server $after 은(는) 이미 최신 버전입니다." }
  elseif ($before) { $head = Say "Kelo Server was updated to $after." "Kelo Server를 $after 버전으로 업데이트했습니다." }
  else { $head = Say "Kelo Server $after is installed." "Kelo Server $after 를 설치했습니다." }
  $msg = "$head " + (Say "The setup page: $url" "설정 페이지: $url")
  Write-KeloResult 'ok' $msg $after $url $opened
  Write-Host "  $msg"
  if (-not $opened -and -not $Elevated) {
    Step (Say 'Opening the setup page' '설정 페이지 여는 중')
    try { Start-Process $url } catch { Step (Say "Open $url in your browser." "브라우저에서 $url 을 여세요.") }
  }
}

function Invoke-KeloServerInstallSafely {
  param([bool]$Elevated, [string]$Stamp)
  try {
    Invoke-KeloServerInstall -Elevated $Elevated -Stamp $Stamp
  } catch {
    Write-Host ''
    Write-Host "  ! $($_.Exception.Message)"
    Write-Host ''
  }
}

Invoke-KeloServerInstallSafely -Elevated ([bool]$Elevated) -Stamp $Stamp
