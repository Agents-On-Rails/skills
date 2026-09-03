#Requires -Version 7
<#
.SYNOPSIS
Installs the publish-safety gate into the clone that contains this script.

.DESCRIPTION
One act, in order, aborting at the first failed guard and writing nothing on abort:
  guards   the clone is not nested inside another git work tree; the denylist exists outside every
           work tree and carries at least one usable line; the scanner binary hashes to the pin held
           in scripts/publish-gate.ps1; pwsh and both root validators (claude, gh) are on PATH;
           core.hooksPath is unset; the remote 'origin' exists.
  hooks    .git/hooks/pre-commit and pre-push, untracked #!/bin/sh stubs that exec pwsh on the
           tracked gate script and forward stdin.
  config   aor.denylist and aor.gitleaks in the clone's local git config.
  push     last: the origin URL forced to HTTPS (when it is a GitHub URL), credential.helper reset
           and then set to a helper that reads the named user's token from the gh keyring.
Idempotent: re-running yields the same state. Removal is a visible act (delete the hooks, unset the
keys). Deliberate bypass (--no-verify, a hand-edited config) stays possible and visible; the threat
model is accidental exposure, not a hostile maintainer.

.PARAMETER GitHubUser
The GitHub login whose gh-keyring token pushes from this clone.

.PARAMETER Denylist
Path of the private identifier list. Default: ~/.aor/publish-denylist.txt.

.PARAMETER Gitleaks
Path of the pinned gitleaks binary. Default: ~/.aor/bin/gitleaks/gitleaks(.exe). Fetch it with
scripts/get-gitleaks.ps1.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$GitHubUser,
  [string]$Denylist = (Join-Path $HOME '.aor' 'publish-denylist.txt'),
  [string]$Gitleaks = (Join-Path $HOME '.aor' 'bin' 'gitleaks' $(if ($IsWindows) { 'gitleaks.exe' } else { 'gitleaks' }))
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Proc([string]$Exe, [string[]]$Arguments, [string]$WorkingDirectory = $null) {
  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $Exe
  foreach ($a in $Arguments) { $psi.ArgumentList.Add($a) }
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
  $psi.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
  if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
  $p = [System.Diagnostics.Process]::Start($psi)
  $err = $p.StandardError.ReadToEndAsync(); $out = $p.StandardOutput.ReadToEndAsync()
  $p.WaitForExit()
  return [pscustomobject]@{ ExitCode = $p.ExitCode; StdOut = $out.Result; StdErr = $err.Result }
}

function Abort([string]$Message) { Write-Host "install-gate: ABORT $Message"; exit 1 }
function Step([string]$Message) { Write-Host "install-gate: $Message" }

function Test-InsideWorkTree([string]$Directory) {
  $r = Invoke-Proc 'git' @('-C', $Directory, 'rev-parse', '--is-inside-work-tree')
  return ($r.ExitCode -eq 0 -and $r.StdOut.Trim() -eq 'true')
}

function Normalize-Path([string]$P) { return [IO.Path]::GetFullPath($P).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) }

# ---- locate the clone this script belongs to ----
$scriptsDir = $PSScriptRoot
$gate = Join-Path $scriptsDir 'publish-gate.ps1'
if (-not (Test-Path -LiteralPath $gate -PathType Leaf)) { Abort 'publish-gate.ps1 is not beside this script' }
$top = Normalize-Path (Split-Path -Parent $scriptsDir)
$r = Invoke-Proc 'git' @('-C', $top, 'rev-parse', '--show-toplevel')
if ($r.ExitCode -ne 0) { Abort 'this script is not inside a git work tree' }
$gitTop = Normalize-Path ($r.StdOut.Trim())
if ([string]::Compare($gitTop, $top, [StringComparison]::OrdinalIgnoreCase) -ne 0) { Abort "this script's repository root ($gitTop) is not the directory above scripts/ ($top)" }
if ($GitHubUser -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$') { Abort 'GitHubUser must be a GitHub login (letters, digits, hyphens)' }

# ---- guards ----
$parent = Split-Path -Parent $top
if ($parent -and (Test-InsideWorkTree $parent)) { Abort "the clone at $top is nested inside another git work tree; move it beside, not inside, other repositories" }
Step 'guard: not nested in another work tree'

if (-not (Test-Path -LiteralPath $Denylist -PathType Leaf)) { Abort "denylist not found at $Denylist (write it by hand: one identifier per line, # comments allowed, UTF-8)" }
$Denylist = Normalize-Path $Denylist
if (Test-InsideWorkTree (Split-Path -Parent $Denylist)) { Abort 'the denylist sits inside a git work tree; it must live outside every repository' }
$usable = 0
foreach ($raw in [IO.File]::ReadAllLines($Denylist, [Text.UTF8Encoding]::new($false))) { $t = $raw.Trim(); if ($t -ne '' -and -not $t.StartsWith('#')) { $usable++ } }
if ($usable -lt 1) { Abort 'the denylist has no usable line (blank and # lines do not count)' }
Step 'guard: denylist present outside every work tree, with usable content'

if (-not (Test-Path -LiteralPath $Gitleaks -PathType Leaf)) { Abort "gitleaks not found at $Gitleaks (run scripts/get-gitleaks.ps1)" }
$Gitleaks = Normalize-Path $Gitleaks
$v = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', $gate, '-VerifyGitleaks', $Gitleaks)
if ($v.ExitCode -ne 0) { Abort "gitleaks at $Gitleaks does not hash to the pinned release (run scripts/get-gitleaks.ps1)" }
Step 'guard: gitleaks hash matches the pin'

foreach ($tool in 'pwsh', 'claude', 'gh') { if (-not (Get-Command $tool -CommandType Application -ErrorAction SilentlyContinue)) { Abort "$tool is not on PATH; the hooks and the pre-push validators need it" } }
Step 'guard: pwsh, claude and gh are on PATH'

$h = Invoke-Proc 'git' @('-C', $top, 'config', '--get', 'core.hooksPath')
if ($h.ExitCode -eq 0) { Abort 'core.hooksPath is set (in some scope); the gate installs into .git/hooks and would never run' }
Step 'guard: core.hooksPath is unset'

$u = Invoke-Proc 'git' @('-C', $top, 'remote', 'get-url', 'origin')
if ($u.ExitCode -ne 0) { Abort "remote 'origin' is not configured" }
$originUrl = $u.StdOut.Trim()
Step 'guard: remote origin exists'

# ---- hooks ----
$hp = Invoke-Proc 'git' @('-C', $top, 'rev-parse', '--git-path', 'hooks')
if ($hp.ExitCode -ne 0) { Abort 'cannot resolve the hooks directory' }
$hooksDir = $hp.StdOut.Trim()
if (-not [IO.Path]::IsPathRooted($hooksDir)) { $hooksDir = Join-Path $top $hooksDir }
$hooksDir = Normalize-Path $hooksDir
New-Item -ItemType Directory -Force -Path $hooksDir | Out-Null
$stubs = @{
  'pre-commit' = "#!/bin/sh`n# Installed by scripts/install-gate.ps1 (Agents-On-Rails publish-safety gate). Untracked; re-run the installer to refresh.`nexec pwsh -NoProfile -NonInteractive -File scripts/publish-gate.ps1 -Hook pre-commit`n"
  'pre-push'   = "#!/bin/sh`n# Installed by scripts/install-gate.ps1 (Agents-On-Rails publish-safety gate). Untracked; re-run the installer to refresh.`nexec pwsh -NoProfile -NonInteractive -File scripts/publish-gate.ps1 -Hook pre-push -Remote `"`$1`" -RemoteUrl `"`$2`"`n"
}
foreach ($name in $stubs.Keys) {
  $file = Join-Path $hooksDir $name
  [IO.File]::WriteAllText($file, $stubs[$name], [Text.UTF8Encoding]::new($false))
  if (-not $IsWindows) { $c = Invoke-Proc 'chmod' @('+x', $file); if ($c.ExitCode -ne 0) { Abort "chmod +x failed on $file" } }
}
Step "hooks written: $hooksDir (pre-commit, pre-push)"

# ---- config keys ----
foreach ($pair in @(@('aor.denylist', $Denylist), @('aor.gitleaks', $Gitleaks))) {
  $c = Invoke-Proc 'git' @('-C', $top, 'config', '--local', $pair[0], $pair[1])
  if ($c.ExitCode -ne 0) { Abort "git config --local $($pair[0]) failed" }
}
Step 'config: aor.denylist and aor.gitleaks set in local config'

# ---- push path, last ----
$https = $null
if ($originUrl -match '^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?/?$') {
  $https = "https://github.com/$($Matches[1])/$($Matches[2]).git"
}
if ($null -ne $https) {
  if ($https -ne $originUrl) {
    $s = Invoke-Proc 'git' @('-C', $top, 'remote', 'set-url', 'origin', $https)
    if ($s.ExitCode -ne 0) { Abort 'git remote set-url failed' }
    Step "push path: origin URL set to $https"
  } else { Step "push path: origin URL already $https" }
} else { Step "push path: origin is not a GitHub URL; left unchanged ($originUrl)" }
$pu = Invoke-Proc 'git' @('-C', $top, 'config', '--local', '--unset-all', 'remote.origin.pushurl')
if ($pu.ExitCode -ne 0 -and $pu.ExitCode -ne 5) { Abort "could not remove remote.origin.pushurl (exit $($pu.ExitCode))" }
if ($pu.ExitCode -eq 0) { Step 'push path: a separate push URL was removed; pushes use the origin URL' }
$un = Invoke-Proc 'git' @('-C', $top, 'config', '--local', '--unset-all', 'credential.helper')
if ($un.ExitCode -ne 0 -and $un.ExitCode -ne 5) { Abort "could not reset credential.helper (exit $($un.ExitCode))" }
$helper = '!f() { echo username=' + $GitHubUser + '; echo "password=$(gh auth token --user ' + $GitHubUser + ')"; }; f'
foreach ($value in @('', $helper)) {
  $a = Invoke-Proc 'git' @('-C', $top, 'config', '--local', '--add', 'credential.helper', $value)
  if ($a.ExitCode -ne 0) { Abort 'git config --add credential.helper failed' }
}
$check = Invoke-Proc 'git' @('-C', $top, 'config', '--local', '--get-all', 'credential.helper')
$got = @($check.StdOut -split "`n" | ForEach-Object { $_.TrimEnd("`r") })
if ($got.Count -lt 2 -or $got[0] -ne '' -or $got[1] -ne $helper) { Abort 'credential.helper did not read back as the reset entry followed by the gh-keyring helper' }
Step "push path: credential.helper reset, then the gh-keyring helper for $GitHubUser"

Write-Host "install-gate: done. Every commit and push from $top now runs scripts/publish-gate.ps1."
exit 0
