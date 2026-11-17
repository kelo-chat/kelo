# Helpers shared by tools/verify/windows-verify.ps1 and windows-repro-check.ps1 (dot-sourced, not run).
# Windows PowerShell 5.1 and PowerShell 7. Nothing here needs more than the PC has: the Java runtime (and, when there is no Python 3, the
# small "embeddable" Python) are downloaded once, checked against the SHA-256 written in apps/desktop/packaging/windows/{JDK,PYTHON}.
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # the progress bar makes Windows PowerShell 5.1 download ten times slower
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
Add-Type -AssemblyName System.IO.Compression.FileSystem

$script:KeloRepo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:KeloCache = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'kelo' } else { Join-Path $env:USERPROFILE '.kelo-cache' }

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor White }
function Write-Note([string]$Text) { Write-Host "    $Text" }
# "could not check" (exit code 2 in windows-verify.ps1)
function Fail([string]$Text) { throw "KELO: $Text" }
function Quote([string]$s) { '"' + $s + '"' }

function Read-Pin([string]$File) {
    if (-not (Test-Path -LiteralPath $File)) { Fail "no pin file: $File" }
    $line = Get-Content -LiteralPath $File | Where-Object { $_ -notmatch '^\s*(#|$)' } | Select-Object -First 1
    $p = @($line.Trim() -split '\s+')
    if ($p.Count -ne 3 -or $p[2] -notmatch '^[0-9a-f]{64}$') { Fail "${File}: expected '<name> <arch> <sha256>', got '$line'" }
    [pscustomobject]@{ Name = $p[0]; Arch = $p[1]; Sha = $p[2] }
}

function Save-Verified([string]$Url, [string]$Dest, [string]$Sha) {
    for ($i = 1; $i -le 3; $i++) {
        try { Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing; break }
        catch { if ($i -eq 3) { Fail "could not download $Url ($($_.Exception.Message))" }; Start-Sleep -Seconds (10 * $i) }
    }
    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $Dest).Hash.ToLower()
    if ($got -ne $Sha) { Remove-Item -Force -LiteralPath $Dest; Fail "the download of $Url has the SHA-256 $got, not the pinned $Sha" }
}

# Runs a program with its output in files; waits at most $TimeoutMin minutes and then stops that program and its children (only the
# process this started). Returns its exit code (124 on timeout).
function Invoke-Native([string]$Exe, [string]$Arguments, [string]$Out, [string]$Dir = (Get-Location).Path, [int]$TimeoutMin = 30) {
    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $Dir -NoNewWindow -PassThru `
        -RedirectStandardOutput $Out -RedirectStandardError "$Out.err"
    $null = $p.Handle
    if (-not $p.WaitForExit($TimeoutMin * 60 * 1000)) {
        & taskkill.exe /T /F /PID $p.Id *> $null
        return 124
    }
    $p.WaitForExit()
    return $p.ExitCode
}

function Install-KeloJdk([string]$PinFile, [string]$Cache = $script:KeloCache) {
    $pin = Read-Pin $PinFile
    if ($pin.Name -notmatch '^temurin-17\.\d+\.\d+\+\d+$' -or $pin.Arch -ne 'x64') { Fail "${PinFile}: expected 'temurin-17.X.Y+Z x64 <sha256>'" }
    $ver = $pin.Name.Substring(8)
    $dir = Join-Path (Join-Path $Cache 'jdk') $pin.Name
    if (-not (Test-Path -LiteralPath (Join-Path $dir 'bin\java.exe'))) {
        $url = 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-' + $ver.Replace('+', '%2B') + '/OpenJDK17U-jdk_x64_windows_hotspot_' + $ver.Replace('+', '_') + '.zip'
        Write-Step "download the pinned JDK $($pin.Name) (about 180 MB, once)"
        $part = Join-Path (Join-Path $Cache 'jdk') ('.download-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $part | Out-Null
        try {
            $zip = Join-Path $part 'jdk.zip'
            Save-Verified $url $zip $pin.Sha
            [IO.Compression.ZipFile]::ExtractToDirectory($zip, (Join-Path $part 'x'))
            $top = Get-ChildItem -LiteralPath (Join-Path $part 'x') -Directory | Select-Object -First 1
            if (-not $top -or -not (Test-Path -LiteralPath (Join-Path $top.FullName 'bin\java.exe'))) { Fail 'unexpected layout in the JDK archive' }
            if (Test-Path -LiteralPath $dir) { Remove-Item -Recurse -Force -LiteralPath $dir }
            Move-Item -LiteralPath $top.FullName -Destination $dir
        } finally { Remove-Item -Recurse -Force -LiteralPath $part -ErrorAction SilentlyContinue }
    }
    $dir
}

# A Python 3.8+ for tools/verify/appdiff.py: the one on the PC, else the embeddable package pinned in packaging/windows/PYTHON.
function Find-Python([string]$PinFile, [string]$Cache = $script:KeloCache) {
    foreach ($name in 'python', 'python3') {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $cmd) { continue }
        $v = ''
        try { $v = (& $cmd.Source --version 2>&1 | Out-String) } catch { }
        if ($v -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 8) { return $cmd.Source }
    }
    $pin = Read-Pin $PinFile
    if ($pin.Name -notmatch '^python-3\.\d+\.\d+$' -or $pin.Arch -ne 'amd64') { Fail "${PinFile}: expected 'python-3.X.Y amd64 <sha256>'" }
    $ver = $pin.Name.Substring(7)
    $dir = Join-Path (Join-Path $Cache 'python') $pin.Name
    if (-not (Test-Path -LiteralPath (Join-Path $dir 'python.exe'))) {
        Write-Step "no Python 3 on this PC: download the pinned embeddable Python $ver (about 10 MB, once)"
        $part = Join-Path (Join-Path $Cache 'python') ('.download-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
        New-Item -ItemType Directory -Force -Path $part | Out-Null
        try {
            $zip = Join-Path $part 'python.zip'
            Save-Verified "https://www.python.org/ftp/python/$ver/python-$ver-embed-amd64.zip" $zip $pin.Sha
            [IO.Compression.ZipFile]::ExtractToDirectory($zip, (Join-Path $part 'x'))
            if (Test-Path -LiteralPath $dir) { Remove-Item -Recurse -Force -LiteralPath $dir }
            Move-Item -LiteralPath (Join-Path $part 'x') -Destination $dir
        } finally { Remove-Item -Recurse -Force -LiteralPath $part -ErrorAction SilentlyContinue }
    }
    Join-Path $dir 'python.exe'
}

# The Java agent in tools/build/zipclock (README there): a fixed time for zip entries, compiled once with the pinned JDK.
function New-KeloZipClock([string]$JavaHome, [string]$Cache = $script:KeloCache) {
    $src = Join-Path $script:KeloRepo 'tools\build\zipclock'
    foreach ($f in 'ZipClock.java', 'MANIFEST.MF') { if (-not (Test-Path -LiteralPath (Join-Path $src $f))) { Fail "no $src\$f (run this from a checkout of the Kelo repository)" } }
    $bytes = [IO.File]::ReadAllBytes((Join-Path $src 'ZipClock.java')) + [IO.File]::ReadAllBytes((Join-Path $src 'MANIFEST.MF'))
    $sum = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)).Replace('-', '').ToLower()).Substring(0, 16)
    $dir = Join-Path (Join-Path $Cache 'zipclock') ($sum + '-' + (Split-Path $JavaHome -Leaf))
    $jar = Join-Path $dir 'zipclock.jar'
    if (-not (Test-Path -LiteralPath $jar)) {
        $tmp = "$dir.tmp"
        if (Test-Path -LiteralPath $tmp) { Remove-Item -Recurse -Force -LiteralPath $tmp }
        New-Item -ItemType Directory -Force -Path (Join-Path $tmp 'classes') | Out-Null
        $log = Join-Path $tmp 'javac.log'
        $rc = Invoke-Native (Join-Path $JavaHome 'bin\javac.exe') ('--add-exports java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED -d ' + (Quote (Join-Path $tmp 'classes')) + ' ' + (Quote (Join-Path $src 'ZipClock.java'))) $log
        if ($rc -ne 0) { Get-Content $log, "$log.err" | Write-Host; Fail 'could not compile the zipclock agent' }
        $rc = Invoke-Native (Join-Path $JavaHome 'bin\jar.exe') ('--create --file ' + (Quote (Join-Path $tmp 'zipclock.jar')) + ' --manifest ' + (Quote (Join-Path $src 'MANIFEST.MF')) + ' --date 2000-01-01T00:00:00Z -C ' + (Quote (Join-Path $tmp 'classes')) + ' .') $log
        if ($rc -ne 0) { Get-Content $log, "$log.err" | Write-Host; Fail 'could not pack the zipclock agent' }
        if (Test-Path -LiteralPath $dir) { Remove-Item -Recurse -Force -LiteralPath $dir }
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Move-Item -LiteralPath (Join-Path $tmp 'zipclock.jar') -Destination $jar
        Remove-Item -Recurse -Force -LiteralPath $tmp
    }
    $jar
}

# The folder that holds apps\desktop\gradlew.bat: $Root itself or one folder below it (a GitHub zip has one top folder).
function Find-SourceRoot([string]$Root) {
    foreach ($c in @($Root) + @(Get-ChildItem -LiteralPath $Root -Directory | ForEach-Object { $_.FullName })) {
        if (Test-Path -LiteralPath (Join-Path $c 'apps\desktop\gradlew.bat')) { return $c }
    }
    Fail "no apps\desktop\gradlew.bat in $Root (that is not a Kelo source folder)"
}

# The source of one commit of the public repository (GitHub's zip of that commit, no git needed), unpacked into $Dest.
function Get-KeloSource([string]$Repo, [string]$Commit, [string]$Dest) {
    if ($Commit -notmatch '^[0-9a-f]{40}$') { Fail "the commit must be 40 hex characters (the full hash), not '$Commit'" }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $zip = Join-Path $Dest 'source.zip'
    $url = "https://github.com/$Repo/archive/$Commit.zip"
    for ($i = 1; $i -le 3; $i++) {
        try { Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing; break }
        catch { if ($i -eq 3) { Fail "could not download $url ($($_.Exception.Message))" }; Start-Sleep -Seconds (10 * $i) }
    }
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, (Join-Path $Dest 'x'))
    Remove-Item -Force -LiteralPath $zip
    Find-SourceRoot (Join-Path $Dest 'x')
}

# The Gradle JVM options of the build (apps/desktop/gradle.properties) plus the zipclock agent: given to Gradle as -Dorg.gradle.jvmargs=...
# (the CI uses it too: desktop-windows.yml, so the published installers are made the same way).
function Get-KeloGradleJvmArgs([string]$DesktopDir, [string]$ZipClockJar) {
    $base = ''
    $props = Join-Path $DesktopDir 'gradle.properties'
    if (Test-Path -LiteralPath $props) {
        $line = Get-Content -LiteralPath $props | Where-Object { $_ -match '^org\.gradle\.jvmargs=' } | Select-Object -First 1
        if ($line) { $base = $line -replace '^org\.gradle\.jvmargs=', '' }
    }
    if (-not $base) { $base = '-Xmx3g' }
    if ($ZipClockJar) { $base += " --add-exports=java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED -javaagent:$ZipClockJar" }
    $base
}

# ./gradlew createDistributable in $Source with the pinned JDK and the zipclock agent; returns the app folder it made (with Kelo.exe).
# The version, the commit and "not modified" are given the way the CI gives them (desktop-windows.yml), because a source zip has no .git.
function Invoke-KeloBuild([string]$Source, [string]$JavaHome, [string]$Version, [string]$Commit, [string]$ZipClockJar, [string]$GradleHome, [string]$Log, [int]$TimeoutMin = 90) {
    $desktop = Join-Path $Source 'apps\desktop'
    $jvm = Get-KeloGradleJvmArgs $desktop $ZipClockJar
    $a = '--no-daemon --console=plain -Pkotlin.compiler.execution.strategy=in-process createDistributable ' + (Quote "-PkeloVersion=$Version")
    $a += ' -PkeloDirty=false'   # no -PkeloCommit: the app links by release tag, so no commit is inside it
    $a += ' ' + (Quote "-Dorg.gradle.jvmargs=$jvm")
    $saved = @{ JAVA_HOME = $env:JAVA_HOME; PATH = $env:PATH; GRADLE_USER_HOME = $env:GRADLE_USER_HOME }
    try {
        $env:JAVA_HOME = $JavaHome
        $env:PATH = (Join-Path $JavaHome 'bin') + ';' + $env:PATH
        if ($GradleHome) { $env:GRADLE_USER_HOME = $GradleHome }
        $rc = Invoke-Native (Join-Path $desktop 'gradlew.bat') $a $Log $desktop $TimeoutMin
    } finally {
        $env:JAVA_HOME = $saved.JAVA_HOME; $env:PATH = $saved.PATH; $env:GRADLE_USER_HOME = $saved.GRADLE_USER_HOME
    }
    if ($rc -ne 0) {
        Get-Content $Log, "$Log.err" -Tail 30 -ErrorAction SilentlyContinue | Write-Host
        Fail "the build failed (exit $rc; log: $Log)"
    }
    $app = Join-Path $desktop 'build\compose\binaries\main\app\Kelo'
    if (-not (Test-Path -LiteralPath (Join-Path $app 'Kelo.exe'))) { Fail "the build made no $app\Kelo.exe" }
    $app
}

# appdiff.py --windows A B: prints the differences, returns its exit code (0 same, 1 differs, 2 could not).
function Invoke-AppDiff([string]$Python, [string]$A, [string]$B, [string]$Work) {
    $out = Join-Path $Work 'appdiff.txt'
    $rc = Invoke-Native $Python ('-I ' + (Quote (Join-Path $script:KeloRepo 'tools\verify\appdiff.py')) + ' --windows ' + (Quote $A) + ' ' + (Quote $B)) $out $Work 30
    $lines = @(Get-Content -LiteralPath $out -ErrorAction SilentlyContinue)
    $lines | Select-Object -First 200 | ForEach-Object { Write-Host "    $_" }
    if ($lines.Count -gt 200) { Write-Host "    ... $($lines.Count - 200) more lines (in $out)" }
    Get-Content -LiteralPath "$out.err" -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
    $rc
}

function Remove-Tree([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Remove-Item -Recurse -Force -LiteralPath $Path -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Path) { & cmd.exe /c rmdir /s /q (Quote $Path) *> $null }     # a path too long for Remove-Item
}
