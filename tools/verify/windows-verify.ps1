<#
.SYNOPSIS
  Checks that the Kelo for Windows you installed or downloaded is built from the public source, file by file
  (docs/verify-builds.md, "Windows").

.DESCRIPTION
    powershell -ExecutionPolicy Bypass -File tools\verify\windows-verify.ps1 <Kelo-1.0.9.msi | Kelo-1.0.9.0.msix | "C:\Program Files\Kelo">

  <Package> is the downloaded MSI (kelo.chat/download/windows-msi), the MSIX, or an installed Kelo folder (the one with Kelo.exe). The
  EXE installer wraps the same files as the MSI; unpack nothing by hand, use the MSI. Run it from a checkout of the Kelo repository (it
  needs tools\verify\appdiff.py and tools\build\zipclock). It installs nothing, needs no administrator rights and no git, and leaves its
  work folder only with -KeepWork.

  What it does:
   1. unpacks the MSI (msiexec /a, an "administrative install" into a temporary folder: nothing is installed) or the MSIX (a zip)
   2. reads from the app's own jar which commit of the source it was built from, and its package number (windows has no tag per package
      number, the app carries the commit; -Commit overrides it)
   3. downloads that commit's source from GitHub (a zip, no git) or takes -Source <folder> (an unpacked zip of the commit)
   4. builds it with the pinned Java runtime (apps\desktop\packaging\windows\JDK: Eclipse Temurin, downloaded once into
      %LOCALAPPDATA%\kelo\jdk and checked against its SHA-256): gradlew createDistributable. The first run downloads about 1 GB of build
      tools and takes 20-40 minutes.
   5. compares the two app folders file by file, and inside every jar entry by entry (tools\verify\appdiff.py --windows). Authenticode
      signatures are removed from both sides first, so only the code and data are compared. Python 3 is taken from the PC, or a small
      pinned one (apps\desktop\packaging\windows\PYTHON) is downloaded.

  Left out of the comparison: Authenticode signatures (a signature cannot be made by anyone but the owner), and the MSIX's own files
  (AppxManifest.xml, AppxSignature.p7x, AppxBlockMap.xml, [Content_Types].xml, AppxMetadata, Assets: the Store's packaging around the app).

  Exit 0: every file is the same. 1: something differs (the list is printed). 2: could not check.

.PARAMETER Package   The MSI, the MSIX, or an installed Kelo folder.
.PARAMETER Commit    The full 40-character commit to build, instead of the one named inside the app.
.PARAMETER Source    An unpacked source folder of that commit (the one that holds apps\desktop), instead of downloading it.
.PARAMETER Repo      GitHub repository to download the source from (default kelo-chat/kelo).
.PARAMETER Work      Work folder (default: a new folder in %TEMP%). Keep its path short: the build has deep folders.
.PARAMETER GradleHome  Gradle's download cache (default: Gradle's own, %USERPROFILE%\.gradle).
.PARAMETER Cache     Where the pinned JDK / Python are kept (default %LOCALAPPDATA%\kelo).
.PARAMETER TimeoutMinutes  Longest time for the build (default 90).
.PARAMETER KeepWork  Do not delete the work folder at the end.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)] [string] $Package = '',
    [string] $Commit = '',
    [string] $Source = '',
    [string] $Repo = 'kelo-chat/kelo',
    [string] $Work = '',
    [string] $GradleHome = '',
    [string] $Cache = '',
    [int] $TimeoutMinutes = 90,
    [switch] $KeepWork,
    [switch] $Help
)
$ErrorActionPreference = 'Stop'
if ($Help -or -not $Package) { Get-Help $PSCommandPath -Detailed; if ($Package -or $Help) { exit 0 } else { exit 2 } }

. (Join-Path $PSScriptRoot 'windows-lib.ps1')
if ($Cache) { $script:KeloCache = $Cache }
$windowsPackaging = Join-Path $script:KeloRepo 'apps\desktop\packaging\windows'
$started = Get-Date
$exit = 2
$ownWork = $false

try {
    if (-not (Test-Path -LiteralPath $Package)) { Fail "no such file or folder: $Package" }
    $Package = (Resolve-Path -LiteralPath $Package).Path
    if (-not $Work) { $Work = Join-Path $env:TEMP ('kv-' + [guid]::NewGuid().ToString('N').Substring(0, 8)); $ownWork = $true }
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    $Work = (Resolve-Path -LiteralPath $Work).Path

    Write-Step '1. unpack the installer'
    $downloaded = Join-Path $Work 'downloaded'
    if (Test-Path -LiteralPath $Package -PathType Container) {
        $searchRoot = $Package
    } else {
        switch ([IO.Path]::GetExtension($Package).ToLower()) {
            '.msi' {
                $log = Join-Path $Work 'msiexec.log'
                $rc = Invoke-Native 'msiexec.exe' ('/a ' + (Quote $Package) + ' /qn TARGETDIR=' + (Quote $downloaded) + ' /L*v ' + (Quote $log)) (Join-Path $Work 'msiexec.out') $Work 15
                if ($rc -ne 0) { Fail "msiexec could not unpack the MSI (exit $rc; log: $log)" }
            }
            '.msix' {
                $root = [IO.Path]::GetFullPath($downloaded).TrimEnd('\') + '\'
                $zip = [IO.Compression.ZipFile]::OpenRead($Package)
                try {
                    foreach ($e in $zip.Entries) {
                        if ($e.FullName.EndsWith('/')) { continue }
                        $target = [IO.Path]::GetFullPath((Join-Path $downloaded ([Uri]::UnescapeDataString($e.FullName).Replace('/', '\'))))   # MSIX names are percent-encoded
                        if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { Fail "the MSIX has an entry outside its folder: $($e.FullName)" }
                        New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
                        [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $target, $true)
                    }
                } finally { $zip.Dispose() }
            }
            '.exe' { Fail 'the EXE installer wraps the same files as the MSI: check the MSI (kelo.chat/download/windows-msi) instead' }
            default { Fail "not an MSI, MSIX or folder: $Package" }
        }
        $searchRoot = $downloaded
    }
    $exe = Get-ChildItem -LiteralPath $searchRoot -Recurse -Filter Kelo.exe -File | Sort-Object { $_.FullName.Length } | Select-Object -First 1
    if (-not $exe) { Fail "no Kelo.exe in $searchRoot" }
    $appDir = $exe.DirectoryName
    Write-Note "the app: $appDir"
    try { Write-Note ('signature of Kelo.exe: ' + (Get-AuthenticodeSignature -LiteralPath $exe.FullName).Status + ' (the installers are not code-signed yet)') } catch { }

    Write-Step '2. which source it was built from'
    $python = Find-Python (Join-Path $windowsPackaging 'PYTHON')
    Write-Note "python: $python"
    $infoFile = Join-Path $Work 'releaseinfo.txt'
    $rc = Invoke-Native $python ('-I ' + (Quote (Join-Path $script:KeloRepo 'tools\verify\appdiff.py')) + ' --release-info ' + (Quote $appDir)) $infoFile $Work 5
    if ($rc -ne 0) { Fail "could not read the version from the app's jars (is this a Kelo app folder?)" }
    $info = @{}
    foreach ($l in Get-Content -LiteralPath $infoFile) { if ($l -match '^([A-Z_]+)=(.*)$') { $info[$Matches[1]] = $Matches[2] } }
    $pkgNumber = $info['PACKAGE']
    if (-not $pkgNumber) { Fail 'the app does not say its package number' }
    $appCommit = $info['SOURCE_COMMIT']
    Write-Note "package number $pkgNumber, release $($info['VERSION']), commit in the app: $(if ($appCommit) { $appCommit } else { '(none)' })$(if ($info['SOURCE_DIRTY'] -eq 'true') { ', built from a modified tree' })"
    if ($Commit -and $Commit -notmatch '^[0-9a-f]{40}$') {
        $git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($git) {
            $resolved = (& $git.Source -C $script:KeloRepo rev-parse --verify "$Commit^{commit}" 2>$null)
            if ($resolved -match '^[0-9a-f]{40}$') { $Commit = $resolved.Trim() }
        }
    }
    if ($Commit -and $appCommit -and $Commit -ne $appCommit) { Write-Note "note: -Commit $Commit is not the commit the app names; the comparison will show the difference" }
    $useCommit = if ($Commit) { $Commit } else { $appCommit }
    if (-not $Source -and -not $useCommit) { Fail 'the app names no source commit: give -Commit <40 hex characters> or -Source <folder>' }
    if ($info['SOURCE_DIRTY'] -eq 'true') { Write-Note 'warning: the app was built from files that differed from its commit; a difference below is then expected' }

    Write-Step '3. the source'
    if ($Source) {
        $src = Find-SourceRoot (Resolve-Path -LiteralPath $Source).Path
        Write-Note "source folder: $src (the app names commit $(if ($appCommit) { $appCommit } else { 'none' }); this folder is taken as that commit)"
    } else {
        Write-Note "download https://github.com/$Repo/archive/$useCommit.zip"
        $src = Get-KeloSource $Repo $useCommit (Join-Path $Work 'src')
    }

    Write-Step '4. build it (unsigned) with the pinned Java runtime'
    $pin = Join-Path $src 'apps\desktop\packaging\windows\JDK'
    if (-not (Test-Path -LiteralPath $pin)) { $pin = Join-Path $windowsPackaging 'JDK' }
    $jdk = Install-KeloJdk $pin
    $zc = New-KeloZipClock $jdk
    Write-Note "JAVA_HOME=$jdk"
    Write-Note "zipclock agent: $zc"
    $buildLog = Join-Path $Work 'build.log'
    Write-Note "gradle createDistributable (log: $buildLog) ..."
    $built = Invoke-KeloBuild $src $jdk $pkgNumber $useCommit $zc $GradleHome $buildLog $TimeoutMinutes

    Write-Step '5. compare, file by file (signatures removed)'
    $rc = Invoke-AppDiff $python $appDir $built $Work
    Write-Host ''
    $label = if ($useCommit) { "commit $useCommit" } else { 'the given source folder' }
    if ($rc -eq 0) {
        Write-Host "Kelo $pkgNumber for Windows: every file is the same as the source at $label, apart from signatures." -ForegroundColor Green
        $exit = 0
    } elseif ($rc -eq 1) {
        Write-Host "Kelo $pkgNumber for Windows: NOT the same as the source at $label. The differences are listed above." -ForegroundColor Yellow
        $exit = 1
    } else { Fail 'the comparison could not run' }
    Write-Note ("took {0:N0} minutes" -f ((Get-Date) - $started).TotalMinutes)
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
