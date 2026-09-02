#Requires -Version 7
<#
.SYNOPSIS
Fetches the gitleaks release pinned in scripts/publish-gate.ps1 and verifies it by hash.

.DESCRIPTION
Downloads the release archive for this platform (Windows x64 or Linux x64), checks its SHA-256
against the pin, extracts it, checks the extracted binary against its pin, and prints the binary's
path on the output stream (status goes to the host). Used locally before scripts/install-gate.ps1
and by CI before every gate run. If a binary that already matches its pin sits at the destination,
nothing is downloaded.
#>
[CmdletBinding()]
param(
  [string]$Destination = (Join-Path $HOME '.aor' 'bin' 'gitleaks')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$gate = Join-Path $PSScriptRoot 'publish-gate.ps1'
$pinsJson = & pwsh -NoProfile -NonInteractive -File $gate -ShowPins
if ($LASTEXITCODE -ne 0) { throw 'could not read the pins from publish-gate.ps1' }
$pins = $pinsJson | ConvertFrom-Json
$ver = [string]$pins.version

if ($IsWindows) { $asset = "gitleaks_${ver}_windows_x64.zip"; $archiveKey = 'windows_x64.zip'; $binary = 'gitleaks.exe'; $binaryKey = 'windows_x64.exe' }
elseif ($IsLinux) { $asset = "gitleaks_${ver}_linux_x64.tar.gz"; $archiveKey = 'linux_x64.tar.gz'; $binary = 'gitleaks'; $binaryKey = 'linux_x64.bin' }
else { throw 'unsupported platform (Windows x64 and Linux x64 only)' }

$expectedArchive = [string]$pins.pins.$archiveKey
$expectedBinary = [string]$pins.pins.$binaryKey
$Destination = [IO.Path]::GetFullPath($Destination)
$binPath = Join-Path $Destination $binary

function Get-Sha256([string]$File) { return (Get-FileHash -Algorithm SHA256 -LiteralPath $File).Hash.ToLowerInvariant() }

if ((Test-Path -LiteralPath $binPath -PathType Leaf) -and ((Get-Sha256 $binPath) -eq $expectedBinary)) {
  Write-Host "get-gitleaks: gitleaks $ver already present and verified at $binPath"
  Write-Output $binPath
  exit 0
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("aor-gitleaks-" + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
  $archive = Join-Path $tmp $asset
  $url = "https://github.com/gitleaks/gitleaks/releases/download/v$ver/$asset"
  Write-Host "get-gitleaks: downloading $url"
  Invoke-WebRequest -Uri $url -OutFile $archive -MaximumRedirection 5
  $actual = Get-Sha256 $archive
  if ($actual -ne $expectedArchive) { throw "archive hash mismatch for $asset (the download does not match the pinned release)" }
  Write-Host 'get-gitleaks: archive hash verified'
  if ($IsWindows) { Expand-Archive -LiteralPath $archive -DestinationPath $Destination -Force }
  else { & tar -xzf $archive -C $Destination; if ($LASTEXITCODE -ne 0) { throw "tar exited $LASTEXITCODE" } }
  if (-not (Test-Path -LiteralPath $binPath -PathType Leaf)) { throw "the archive did not contain $binary" }
  $actualBin = Get-Sha256 $binPath
  if ($actualBin -ne $expectedBinary) { Remove-Item -LiteralPath $binPath -Force; throw 'binary hash mismatch after extraction' }
  if (-not $IsWindows) { & chmod +x $binPath }
  Write-Host "get-gitleaks: gitleaks $ver installed and verified at $binPath"
  Write-Output $binPath
} finally {
  if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
}
exit 0
