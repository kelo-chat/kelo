<#
.SYNOPSIS
  Builds one commit of Kelo for Windows twice (in two different folders) and compares the two apps file by file: the build must be
  the same every time (docs/verify-builds.md, "Windows"). The Windows twin of mac-repro-check.sh.

.DESCRIPTION
    powershell -ExecutionPolicy Bypass -File tools\verify\windows-repro-check.ps1 -Commit <40 hex> | -Source <folder> [-Work C:\kv] [-GradleHome C:\kv\gradle]

  Uses the pinned Java runtime and the zipclock agent like windows-verify.ps1. Exit 0: the two builds are identical, 1: they differ
  (the list is printed, jar times included), 2: could not check. Takes two builds' time (2 x 15-30 minutes).
#>
[CmdletBinding()]
param(
    [string] $Commit = '',
    [string] $Source = '',
    [string] $Repo = 'kelo-chat/kelo',
    [string] $Work = '',
    [string] $GradleHome = '',
    [string] $Cache = '',
    [switch] $NoZipClock,
    [int] $TimeoutMinutes = 90,
    [switch] $KeepWork
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows-lib.ps1')
if ($Cache) { $script:KeloCache = $Cache }
$exit = 2
$ownWork = $false
try {
    if (-not $Commit -and -not $Source) { Fail 'give -Commit <40 hex characters> or -Source <folder>' }
    if ($Commit -and $Commit -notmatch '^[0-9a-f]{40}$') {
        $git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($git) {
            $resolved = (& $git.Source -C $script:KeloRepo rev-parse --verify "$Commit^{commit}" 2>$null)
            if ($resolved -match '^[0-9a-f]{40}$') { $Commit = $resolved.Trim() }
        }
    }
    if (-not $Work) { $Work = Join-Path $env:TEMP ('kr-' + [guid]::NewGuid().ToString('N').Substring(0, 8)); $ownWork = $true }
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    $Work = (Resolve-Path -LiteralPath $Work).Path
    $python = Find-Python (Join-Path $script:KeloRepo 'apps\desktop\packaging\windows\PYTHON')
    $jdk = Install-KeloJdk (Join-Path $script:KeloRepo 'apps\desktop\packaging\windows\JDK')
    $zc = New-KeloZipClock $jdk
    if ($Source) { $one = Find-SourceRoot (Resolve-Path -LiteralPath $Source).Path } else { $one = Get-KeloSource $Repo $Commit (Join-Path $Work 'dl') }
    $info = (Get-Content -LiteralPath (Join-Path $one 'apps\desktop\version.properties') | Where-Object { $_ -match '^packageVersion=' } | Select-Object -First 1)
    $pkgNumber = ($info -replace '^packageVersion=', '').Trim()
    $apps = @()
    foreach ($name in 'one', 'second-folder') {
        Write-Step "build in $name"
        $dir = Join-Path $Work $name
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Copy-Item -Recurse -Force -LiteralPath $one -Destination (Join-Path $dir 'src')
        $zipArg = if ($NoZipClock) { '' } else { $zc }
        $apps += Invoke-KeloBuild (Join-Path $dir 'src') $jdk $pkgNumber $Commit $zipArg $GradleHome (Join-Path $dir 'build.log') $TimeoutMinutes
    }
    Write-Step 'compare the two builds'
    $rc = Invoke-AppDiff $python $apps[0] $apps[1] $Work
    if ($rc -eq 0) { Write-Host 'the two builds are identical' -ForegroundColor Green; $exit = 0 }
    elseif ($rc -eq 1) { Write-Host 'the two builds DIFFER (listed above)' -ForegroundColor Yellow; $exit = 1 }
    else { Fail 'the comparison could not run' }
}
catch {
    $m = $_.Exception.Message
    if ($m.StartsWith('KELO: ')) { $m = $m.Substring(6) }
    Write-Host "error: $m" -ForegroundColor Red
    $exit = 2
}
finally {
    if ($ownWork -and -not $KeepWork) { Remove-Tree $Work }
}
exit $exit
