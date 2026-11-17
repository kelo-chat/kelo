# tools/build/server-windows-bundle.ps1
# Builds the single setup .exe for a fresh Windows 10/11 x64 PC (WiX v4 Burn bundle).
#
# The bundle installs Microsoft's signed WSL package first when WSL is missing, handles the restart WSL may need (and resumes
# after it), then runs the Kelo Server MSI. The MSI itself is built by tools/build/server-windows.ps1 (run here when missing).
#
# Output: dist/KeloServer-Setup-<version>-x64.exe
# Requirements: Windows PowerShell 5.1+ or pwsh, wix (.NET tool: dotnet tool install --global wix)

[CmdletBinding()]
param (
    [string]$DistroTar = "",
    [string]$WslVersion = "3.0.5",
    [string]$WslMsi = "",
    [string]$VersionOverride = "",
    [string]$KeloMsi = "",
    [string]$CertThumbprint = "",
    [string]$CertPath = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DistDir = Join-Path $RepoRoot "dist"
if (-not (Test-Path $DistDir)) { New-Item -ItemType Directory -Force -Path $DistDir | Out-Null }

# 1. Version
$Version = $VersionOverride
if (-not $Version) {
    $VersionFile = Join-Path $RepoRoot "version.json"
    $Version = if (Test-Path $VersionFile) { (Get-Content -Raw $VersionFile | ConvertFrom-Json).version } else { "0.1.0" }
}
Write-Host "Building Kelo Server $Version setup (.exe) for Windows x64..."

# 2. Kelo MSI: build it when it is not there
if (-not $KeloMsi) { $KeloMsi = Join-Path $DistDir "KeloServer-$Version-x64.msi" }
if (-not (Test-Path $KeloMsi)) {
    Write-Host "Kelo MSI not found: building it first..."
    $msiArgs = @{ VersionOverride = $Version }
    if ($DistroTar) { $msiArgs.DistroTar = $DistroTar }
    & (Join-Path $PSScriptRoot "server-windows.ps1") @msiArgs
    if (-not (Test-Path $KeloMsi)) { Write-Error "The Kelo MSI was not built: $KeloMsi"; exit 1 }
}
$KeloMsi = (Resolve-Path $KeloMsi).Path
Write-Host "Using Kelo MSI: $KeloMsi"

# 3. Microsoft's WSL package: Authenticode (Microsoft) and the SHA-256 pinned in the install script
if (-not $WslMsi -or -not (Test-Path $WslMsi)) {
    $WslMsi = Join-Path $DistDir "wsl.$WslVersion.0.x64.msi"
    if (-not (Test-Path $WslMsi)) {
        $wslUrl = "https://github.com/microsoft/WSL/releases/download/$WslVersion/wsl.$WslVersion.0.x64.msi"
        Write-Host "Downloading Microsoft WSL $WslVersion MSI from $wslUrl..."
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -UseBasicParsing -Uri $wslUrl -OutFile $WslMsi
    }
}
$WslMsi = (Resolve-Path $WslMsi).Path
Write-Host "Checking Authenticode signature of WSL MSI..."
$sig = Get-AuthenticodeSignature $WslMsi
if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "^CN=Microsoft Corporation,") {
    Write-Error "The WSL package is not signed by Microsoft (Status: $($sig.Status)); refusing to put it in the setup."
    exit 1
}
$installText = Get-Content -Raw (Join-Path $RepoRoot "scripts\windows\kelo-wsl-install.ps1")
$pinned = [regex]::Match($installText, '\[string\]\$WslMsiSha256 = "([0-9a-f]{64})"').Groups[1].Value
$actual = (Get-FileHash -Path $WslMsi -Algorithm SHA256).Hash.ToLower()
if (-not $pinned -or $actual -ne $pinned) {
    Write-Error "WSL MSI SHA-256 is $actual but the install script pins '$pinned'; refusing to put it in the setup."
    exit 1
}
Write-Host "WSL MSI signature is valid ($($sig.SignerCertificate.Subject)) and SHA-256 matches the pin."

# 4. Build the bundle with WiX v4 (Bal = the setup screen and conditions, Util = file and registry searches)
$PackagingDir = (Resolve-Path (Join-Path $RepoRoot "packaging\windows")).Path
$WxsFile = Join-Path $PackagingDir "KeloBundle.wxs"
$ExeOutput = Join-Path $DistDir "KeloServer-Setup-$Version-x64.exe"
$wixMajor = ((wix --version) -split '\.')[0]
foreach ($ext in @("WixToolset.Bal.wixext", "WixToolset.Util.wixext")) {
    if (-not ((wix extension list -g) -match [regex]::Escape($ext))) { wix extension add -g "$ext/$wixMajor" | Out-Null }
}
Write-Host "Building setup with WiX v4: $ExeOutput..."
wix build -arch x64 `
    -d Version=$Version `
    -d KeloMsi=$KeloMsi `
    -d WslMsi=$WslMsi `
    -d PackagingDir=$PackagingDir `
    -ext WixToolset.Bal.wixext `
    -ext WixToolset.Util.wixext `
    $WxsFile `
    -o $ExeOutput
if ($LASTEXITCODE -ne 0) {
    Write-Error "WiX build failed with exit code $LASTEXITCODE."
    exit $LASTEXITCODE
}

# 5. Authenticode signing: the engine is signed first, then the bundle (a bundle .exe carries its engine inside)
$signtoolCmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
$signtool = if ($signtoolCmd) { $signtoolCmd.Source } else { $null }
if (-not $signtool) {
    $sdkMatches = @(Get-Item "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue)
    if ($sdkMatches.Count -gt 0) { $signtool = $sdkMatches[-1].FullName }
}
if ($signtool -and ($CertThumbprint -or $CertPath)) {
    $signParams = @("sign", "/fd", "SHA256", "/tr", "http://timestamp.digicert.com", "/td", "SHA256")
    if ($CertThumbprint) { $signParams += @("/sha1", $CertThumbprint) } else { $signParams += @("/f", $CertPath) }
    $engine = Join-Path $DistDir "burn-engine.exe"
    wix burn detach $ExeOutput -engine $engine
    & $signtool @signParams $engine
    wix burn reattach $ExeOutput -engine $engine -o $ExeOutput
    Remove-Item -Force $engine -ErrorAction SilentlyContinue
    & $signtool @signParams $ExeOutput
} else {
    Write-Host "Code signing note: not signed (no certificate given). Use -CertThumbprint or -CertPath; signtool.exe sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256"
}

$hash = (Get-FileHash -Path $ExeOutput -Algorithm SHA256).Hash
Write-Host "Setup build complete: $ExeOutput"
Write-Host "SHA256: $hash"
