#Requires -Version 7
<#
.SYNOPSIS
Publish-safety gate for the Agents-On-Rails skills repository.

.DESCRIPTION
One script, three placements: the local pre-commit hook (the staged tree), the local pre-push hook
(every commit in the push range, plus the tree-shape, version and tag checks) and CI (the same
checks over the pushed range, with the private list arriving from an organisation secret).
A fourth, ad-hoc mode checks one plugin directory anywhere (-Path).

Two instruments. The LITERAL layer runs `git grep -F` over a private list of identifiers that must
never be published; the list lives outside every repository (path in local git config) or in CI
as an environment variable read through stdin; the gate refuses to run without it and never prints
a matched value. The PATTERN layer runs a hash-pinned gitleaks binary with the tracked
.gitleaks.toml, twice: once with gitleaks' default rules and default allowlist, once with only
this repository's own rules and no inherited allowlist, so a file type the default allowlist skips
is still scanned for this repository's own patterns; file names and commit metadata pass through
the same rules.

Tree rules make process noise structurally impossible: a fixed set of permitted top-level entries,
a noise rule on file base names, SKILL.md placement with unique names, a plugin manifest rule, a
per-file size cap and no binary files, all enumerated through git. The noise, size and binary
rules also run over every commit tree in a push range, not only the tip.

Every run first proves its own detection with a positive control and re-hashes the scanner.
Errors are failures, never empty results.

.NOTES
Public patterns are spelled only in .gitleaks.toml; this script builds its control samples from
pieces at run time and describes the classes in words, because this file is scanned too.
#>
[CmdletBinding()]
param(
  # Hook placement: 'pre-commit' or 'pre-push'. Git runs the hook with the work tree root as the
  # current directory; pre-push passes the remote name and URL and the ref lines on stdin.
  [ValidateSet('pre-commit', 'pre-push')]
  [string]$Hook,
  [string]$Remote,
  [string]$RemoteUrl,

  # CI placement: reads GITHUB_EVENT_NAME, GITHUB_REF, GITHUB_SHA, GITHUB_BASE_REF and the event
  # payload at GITHUB_EVENT_PATH; the list from AOR_PUBLISH_DENYLIST; the scanner from AOR_GITLEAKS.
  [switch]$Ci,

  # Ad-hoc placement: treat one directory as a plugin directory (rules 2 to 6 with
  # plugins/<basename>/ as the virtual prefix; no top-level set, no marketplace check).
  [string]$Path,

  # Maintenance entry points used by the installer and the fetch script.
  [switch]$ShowPins,
  [string]$VerifyGitleaks
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }

# ------------------------------------------------------------------------------------------------
# Pins and constants
# ------------------------------------------------------------------------------------------------

# gitleaks release pins. The archive hashes are copied by script from the release's published
# checksums file; the binary hashes are computed from the archives those checksums verified.
# Upgrading gitleaks is one commit that changes every value here.
$script:GitleaksVersion = '8.30.1'
$script:GitleaksPins = @{
  'windows_x64.zip'  = 'd29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e'
  'linux_x64.tar.gz' = '551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb'
  'windows_x64.exe'  = '17157e2ee8b76fc8b1d8bee607a250e34b8a8023c8bc81822d4b5ee4d78fcb7c'
  'linux_x64.bin'    = '88f91962aa2f93ac6ab281d553b9e125f5197bbbce38f9f2437f7299c32e5509'
}

# The number of allowlist entries .gitleaks.toml may hold (global or rule-scoped, any spelling).
# Widening is a two-file commit: the entry and this number, named in the commit message.
$script:PinnedAllowlistEntries = 0

$script:MaxFileBytes = 1048576
$script:TopLevelDirs = @('.claude-plugin', 'plugins', 'legacy', 'scripts', '.github')
$script:TopLevelFiles = @('README.md', 'LICENSE', '.gitignore', '.gitattributes', '.gitleaks.toml')
$script:NoiseGlobs = @('*handover*', '*payload*', '*readout*', '*panel*', '*-record*.md', 'session-log*', '*.log', '*.jsonl', '*.bak*')
$script:NoiseDirs = @('captures', '__pycache__')
$script:PatternConfigName = '.gitleaks.toml'
$script:EmptyTree = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
$script:ControlWrapper = 'control {0} control'
$script:IncidentMode = ($env:AOR_GATE_INCIDENT -eq '1')
$script:ExitCode = 1

# Rule ids this repository's pattern config must carry, each with a sample built from pieces.
function Get-PatternSamples {
  $bs = [string][char]92
  $samples = [ordered]@{}
  $samples['aor-windows-user-path'] = 'C:' + $bs + 'Us' + 'ers' + $bs + 'jd'
  $samples['aor-posix-user-path'] = ' /' + 'ho' + 'me' + '/jd'
  $samples['aor-synced-folder-path'] = 'One' + 'Drive'
  $samples['aor-claude-project-slug'] = 'C--' + 'Us' + 'ers-' + 'x'
  $samples['aor-unresolved-placeholder'] = '<' + 'AOR' + '_X>'
  return $samples
}

# ------------------------------------------------------------------------------------------------
# Output and failure collection
# ------------------------------------------------------------------------------------------------

$script:Failures = [System.Collections.Generic.List[string]]::new()
$script:Denylist = $null

function Write-Gate([string]$Message) { Write-Host "gate: $Message" }

function Add-Failure([string]$Message) {
  $script:Failures.Add($Message)
  Write-Host "gate: FAIL $Message"
}

function Test-ContainsListed([string]$Text) {
  if ($null -eq $script:Denylist -or [string]::IsNullOrEmpty($Text)) { return $false }
  foreach ($lit in $script:Denylist) {
    if ($Text.IndexOf($lit, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
  }
  return $false
}

# Every path, name or free text this script prints passes through here.
function Format-Safe([string]$Text) {
  if (Test-ContainsListed $Text) { return '<withheld: contains a listed identifier>' }
  return $Text
}

# ------------------------------------------------------------------------------------------------
# Native process helper (explicit stdin bytes, explicit encodings, no shell)
# ------------------------------------------------------------------------------------------------

function Invoke-Native {
  param(
    [Parameter(Mandatory)][string]$Exe,
    [string[]]$Arguments = @(),
    [string[]]$StdinLines = $null,
    [string]$StdinText = $null,
    [string]$WorkingDirectory = $null,
    [hashtable]$Environment = $null
  )
  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  # On Windows an npm-installed validator is a .cmd shim, which CreateProcess cannot start by bare name.
  # Only the two validators may take the command-interpreter path, and only with shell-safe arguments:
  # cmd.exe interprets metacharacters, so git and pwsh (repository-derived arguments) never go through it.
  $file = $Exe
  $argv = @($Arguments)
  if ($IsWindows -and $Exe -in @('claude', 'gh')) {
    $app = Get-Command -Name $Exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $app -and $app.Source -match '\.(cmd|bat)$') {
      foreach ($a in $argv) { if ($a -match '[\s"&|<>^%!()]') { throw "refusing to pass an argument with shell metacharacters to $Exe through the command interpreter" } }
      $file = $env:ComSpec
      $argv = @('/d', '/c', $app.Source) + $argv
    }
  }
  $psi.FileName = $file
  foreach ($a in $argv) { $psi.ArgumentList.Add($a) }
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $utf8 = [Text.UTF8Encoding]::new($false)
  $psi.StandardInputEncoding = $utf8
  $psi.StandardOutputEncoding = $utf8
  $psi.StandardErrorEncoding = $utf8
  if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
  if ($null -ne $Environment) {
    foreach ($k in $Environment.Keys) {
      if ($null -eq $Environment[$k]) { [void]$psi.Environment.Remove($k) } else { $psi.Environment[$k] = [string]$Environment[$k] }
    }
  }
  $p = [System.Diagnostics.Process]::Start($psi)
  $errTask = $p.StandardError.ReadToEndAsync()
  $outTask = $p.StandardOutput.ReadToEndAsync()
  if ($null -ne $StdinLines) { foreach ($l in $StdinLines) { $p.StandardInput.Write($l); $p.StandardInput.Write("`n") } }
  if (-not [string]::IsNullOrEmpty($StdinText)) { $p.StandardInput.Write($StdinText) }
  $p.StandardInput.Close()
  $p.WaitForExit()
  return [pscustomobject]@{ ExitCode = $p.ExitCode; StdOut = $outTask.Result; StdErr = $errTask.Result }
}

# git with "errors are failures": any exit code outside -AllowedExit throws.
function Invoke-Git {
  param(
    [Parameter(Mandatory)][string[]]$Arguments,
    [string[]]$StdinLines = $null,
    [int[]]$AllowedExit = @(0),
    [string]$WorkingDirectory = $null,
    [hashtable]$Environment = $null
  )
  $r = Invoke-Native -Exe 'git' -Arguments $Arguments -StdinLines $StdinLines -WorkingDirectory $WorkingDirectory -Environment $Environment
  if ($AllowedExit -notcontains $r.ExitCode) {
    $shown = @($Arguments)
    if ($shown.Count -ge 2 -and $shown[0] -eq '-C') { $shown = if ($shown.Count -gt 2) { @($shown[2..($shown.Count - 1)]) } else { @() } }
    $first = ($shown | Select-Object -First 2) -join ' '
    throw "git $first exited $($r.ExitCode): $(Format-Safe ($r.StdErr.Trim()))"
  }
  return $r
}

function Get-GitLines([string[]]$Arguments, [string]$WorkingDirectory = $null) {
  $r = Invoke-Git -Arguments $Arguments -WorkingDirectory $WorkingDirectory
  return ,@($r.StdOut -split "`n" | ForEach-Object { $_.TrimEnd("`r") } | Where-Object { $_ -ne '' })
}

function Get-GitNulSeparated([string[]]$Arguments, [string]$WorkingDirectory = $null) {
  $r = Invoke-Git -Arguments $Arguments -WorkingDirectory $WorkingDirectory
  return ,@($r.StdOut -split "`0" | Where-Object { $_ -ne '' })
}

# ------------------------------------------------------------------------------------------------
# Semver
# ------------------------------------------------------------------------------------------------

function ConvertTo-SemVer([string]$Text) {
  if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
  $m = [regex]::Match($Text.Trim(), '^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$')
  if (-not $m.Success) { return $null }
  return [pscustomobject]@{
    Major = [int]$m.Groups[1].Value; Minor = [int]$m.Groups[2].Value; Patch = [int]$m.Groups[3].Value
    Pre   = $m.Groups[4].Value
  }
}

# Returns -1, 0 or 1 for A < B, A = B, A > B.
function Compare-SemVer($A, $B) {
  foreach ($k in 'Major', 'Minor', 'Patch') {
    if ($A.$k -lt $B.$k) { return -1 }
    if ($A.$k -gt $B.$k) { return 1 }
  }
  $aPre = [string]$A.Pre; $bPre = [string]$B.Pre
  if ($aPre -eq '' -and $bPre -eq '') { return 0 }
  if ($aPre -eq '') { return 1 }
  if ($bPre -eq '') { return -1 }
  $ai = $aPre -split '\.'; $bi = $bPre -split '\.'
  $n = [Math]::Min($ai.Count, $bi.Count)
  for ($i = 0; $i -lt $n; $i++) {
    $x = $ai[$i]; $y = $bi[$i]
    $xn = $x -match '^\d+$'; $yn = $y -match '^\d+$'
    if ($xn -and $yn) { $c = [Math]::Sign([int64]$x - [int64]$y); if ($c -ne 0) { return $c }; continue }
    if ($xn) { return -1 }
    if ($yn) { return 1 }
    $c = [string]::CompareOrdinal($x, $y); if ($c -ne 0) { return [Math]::Sign($c) }
  }
  return [Math]::Sign($ai.Count - $bi.Count)
}

# ------------------------------------------------------------------------------------------------
# Scanner pin
# ------------------------------------------------------------------------------------------------

function Get-BinaryPinKey {
  if ($IsWindows) { return 'windows_x64.exe' }
  if ($IsLinux) { return 'linux_x64.bin' }
  throw 'unsupported platform for the pinned scanner (Windows x64 and Linux x64 only)'
}

function Test-GitleaksPinned([string]$Exe) {
  if ([string]::IsNullOrWhiteSpace($Exe) -or -not (Test-Path -LiteralPath $Exe -PathType Leaf)) { return $false }
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Exe).Hash.ToLowerInvariant()
  return ($actual -eq $script:GitleaksPins[(Get-BinaryPinKey)])
}

# ------------------------------------------------------------------------------------------------
# Denylist
# ------------------------------------------------------------------------------------------------

function ConvertTo-Denylist([string[]]$RawLines) {
  $seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  $out = [System.Collections.Generic.List[string]]::new()
  foreach ($raw in $RawLines) {
    if ($null -eq $raw) { continue }
    $line = $raw.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { continue }
    if ($seen.Add($line)) { $out.Add($line) }
  }
  return ,$out.ToArray()
}

function Test-InsideWorkTree([string]$Directory) {
  $r = Invoke-Native -Exe 'git' -Arguments @('-C', $Directory, 'rev-parse', '--is-inside-work-tree')
  return ($r.ExitCode -eq 0 -and $r.StdOut.Trim() -eq 'true')
}

# Sets $script:Denylist or leaves it $null; returns a status line (never a count).
function Initialize-Denylist([string]$RepoTop, [bool]$FromEnvironment) {
  if ($FromEnvironment) {
    $raw = $env:AOR_PUBLISH_DENYLIST
    if ([string]::IsNullOrEmpty($raw)) { return 'denylist not loaded (AOR_PUBLISH_DENYLIST is empty or absent)' }
    $list = ConvertTo-Denylist ($raw -split "`n")
    if ($list.Count -eq 0) { return 'denylist not loaded (no usable line in AOR_PUBLISH_DENYLIST)' }
    $script:Denylist = $list
    return 'denylist loaded'
  }
  $r = Invoke-Native -Exe 'git' -Arguments @('-C', $RepoTop, 'config', '--local', '--get', 'aor.denylist')
  if ($r.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($r.StdOut)) { return 'denylist not loaded (aor.denylist is not set in this clone; run scripts/install-gate.ps1)' }
  $file = $r.StdOut.Trim()
  if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return 'denylist not loaded (the configured file is missing)' }
  if (Test-InsideWorkTree (Split-Path -Parent $file)) { return 'denylist not loaded (the configured file sits inside a git work tree)' }
  $list = ConvertTo-Denylist ([IO.File]::ReadAllLines($file, [Text.UTF8Encoding]::new($false)))
  if ($list.Count -eq 0) { return 'denylist not loaded (no usable line in the configured file)' }
  $script:Denylist = $list
  return 'denylist loaded'
}

# ------------------------------------------------------------------------------------------------
# Pattern config
# ------------------------------------------------------------------------------------------------

function Get-RuleIds([string]$Toml) {
  $ids = [System.Collections.Generic.List[string]]::new()
  $inRule = $false
  foreach ($line in ($Toml -split "`n")) {
    $t = $line.Trim()
    if ($t -match '^\[\[rules\]\]$') { $inRule = $true; continue }
    if ($t -match '^\[') { $inRule = $false; continue }
    if ($inRule -and $t -match '^id\s*=\s*"([^"]+)"') { $ids.Add($Matches[1]) }
  }
  return ,$ids.ToArray()
}

# Counts every allowlist table in any spelling and checks the shape of each global entry.
function Test-AllowlistShape([string]$Toml) {
  $lines = @($Toml -split "`n" | ForEach-Object { $_.TrimEnd("`r") })
  $count = 0
  $problems = [System.Collections.Generic.List[string]]::new()
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $t = $lines[$i].Trim()
    if ($t -match '^\[\[?\s*(rules\.)?allowlists?\s*\]\]?$') {
      $count++
      if ($t -ne '[[allowlists]]') { $problems.Add("allowlist entry at line $($i + 1) must be spelled [[allowlists]]"); continue }
      $body = [System.Collections.Generic.List[string]]::new()
      for ($j = $i + 1; $j -lt $lines.Count; $j++) {
        $u = $lines[$j].Trim()
        if ($u -match '^\[') { break }
        if ($u -ne '' -and -not $u.StartsWith('#')) { $body.Add($u) }
      }
      if ($body.Count -eq 0 -or $body[0] -notmatch '^description\s*=\s*"[^"]{8,}"') { $problems.Add("allowlist entry at line $($i + 1) must start with a description of at least 8 characters") }
      $scoped = $false
      foreach ($b in $body) { if ($b -match '^(paths|regexes|targetRules|commits)\s*=') { $scoped = $true } }
      if (-not $scoped) { $problems.Add("allowlist entry at line $($i + 1) is not scoped (paths, regexes, targetRules or commits)") }
    }
  }
  return [pscustomobject]@{ Count = $count; Problems = $problems.ToArray() }
}

# The own-only pass: the tracked config without its [extend] table, so gitleaks' default rules and
# default allowlist are absent and every file type is scanned for this repository's own patterns.
# One allowlist is synthesized for that pass alone: the pattern config file at the root, which is
# the one file that spells the patterns (the default allowlist grants the same in the other pass).
function ConvertTo-OwnOnlyConfig([string]$Toml) {
  $out = [System.Collections.Generic.List[string]]::new()
  $skip = $false
  foreach ($line in ($Toml -split "`n")) {
    $t = $line.TrimEnd("`r").Trim()
    if ($t -eq '[extend]') { $skip = $true; continue }
    if ($skip -and $t -match '^\[') { $skip = $false }
    if (-not $skip) { $out.Add($line.TrimEnd("`r")) }
  }
  $out.Add('')
  $out.Add('[[allowlists]]')
  $out.Add('description = "own-only pass: the pattern config at the root is the one file that spells the patterns"')
  $out.Add("paths = ['''^\.gitleaks\.toml$''']")
  return ($out -join "`n")
}

# ------------------------------------------------------------------------------------------------
# gitleaks
# ------------------------------------------------------------------------------------------------

function Invoke-Gitleaks {
  param(
    [Parameter(Mandatory)][string]$Exe,
    [Parameter(Mandatory)][string[]]$Arguments,
    [Parameter(Mandatory)][string]$ConfigToml,
    [string]$StdinText = $null,
    [string]$WorkingDirectory = $null
  )
  $common = @('--redact=100', '--exit-code', '2', '--no-banner', '--ignore-gitleaks-allow', '--report-format', 'json', '--report-path', '-', '--log-level', 'error')
  $envMap = @{ GITLEAKS_CONFIG_TOML = $ConfigToml; GITLEAKS_CONFIG = $null }
  $r = Invoke-Native -Exe $Exe -Arguments ($Arguments + $common) -StdinText $StdinText -WorkingDirectory $WorkingDirectory -Environment $envMap
  if ($r.ExitCode -ne 0 -and $r.ExitCode -ne 2) {
    throw "gitleaks exited $($r.ExitCode): $(Format-Safe ($r.StdErr.Trim()))"
  }
  $findings = @()
  $text = $r.StdOut.Trim()
  if ($text -ne '' -and $text -ne '[]') {
    try { $findings = @($text | ConvertFrom-Json) } catch { throw "gitleaks report could not be parsed: $($_.Exception.GetType().Name)" }
  }
  if ($r.ExitCode -eq 2 -and $findings.Count -eq 0) { throw 'gitleaks reported leaks but the report is empty' }
  return ,$findings
}

# ------------------------------------------------------------------------------------------------
# Tree listing (from a tree object or from the working tree of one directory)
# ------------------------------------------------------------------------------------------------

# Entry: Path (repo-relative, forward slashes), Blob (sha or $null), Size, IsBinary, IsGitlink, Source ('tree'|'work'), Local (absolute file path for 'work')
function New-Entry([string]$Path, [string]$Blob, [int64]$Size, [bool]$IsBinary, [bool]$IsGitlink, [string]$Source, [string]$Local) {
  return [pscustomobject]@{ Path = $Path; Blob = $Blob; Size = $Size; IsBinary = $IsBinary; IsGitlink = $IsGitlink; Source = $Source; Local = $Local }
}

function Get-TreeEntries([string]$Tree, [string]$RepoTop) {
  $entries = [System.Collections.Generic.List[object]]::new()
  $raw = Get-GitNulSeparated @('-C', $RepoTop, 'ls-tree', '-r', '-l', '-z', $Tree)
  foreach ($rec in $raw) {
    # <mode> SP <type> SP <sha> SP <size> TAB <path>
    $tab = $rec.IndexOf("`t")
    if ($tab -lt 0) { continue }
    $meta = $rec.Substring(0, $tab) -split '\s+'
    $p = $rec.Substring($tab + 1)
    if ($meta[1] -eq 'commit') { $entries.Add((New-Entry $p $null 0 $false $true 'tree' $null)); continue }
    if ($meta[1] -ne 'blob') { continue }
    $entries.Add((New-Entry $p $meta[2] ([int64]$meta[3]) $false $false 'tree' $null))
  }
  $bin = Get-GitNulSeparated @('-C', $RepoTop, 'diff', '--numstat', '--no-renames', '-z', $script:EmptyTree, $Tree)
  # numstat -z: "<added>\t<deleted>\t<path>" records
  $binary = [System.Collections.Generic.HashSet[string]]::new()
  foreach ($rec in $bin) { if ($rec -match '^-\t-\t(.+)$') { [void]$binary.Add($Matches[1]) } }
  foreach ($e in $entries) { if ($binary.Contains($e.Path)) { $e.IsBinary = $true } }
  return ,$entries.ToArray()
}

function Get-WorkEntries([string]$RepoTop, [string]$RelDir) {
  $entries = [System.Collections.Generic.List[object]]::new()
  $staged = Get-GitNulSeparated @('-C', $RepoTop, 'ls-files', '-s', '-z', '--', $RelDir)
  $binary = [System.Collections.Generic.HashSet[string]]::new()
  $bin = Get-GitNulSeparated @('-C', $RepoTop, 'diff', '--numstat', '--no-renames', '-z', $script:EmptyTree, '--', $RelDir)
  foreach ($rec in $bin) { if ($rec -match '^-\t-\t(.+)$') { [void]$binary.Add($Matches[1]) } }
  foreach ($rec in $staged) {
    # <mode> SP <sha> SP <stage> TAB <path>
    $tab = $rec.IndexOf("`t")
    if ($tab -lt 0) { continue }
    $mode = ($rec.Substring(0, $tab) -split '\s+')[0]
    $p = $rec.Substring($tab + 1)
    if ($mode -eq '160000') { $entries.Add((New-Entry $p $null 0 $false $true 'work' $null)); continue }
    $local = Join-Path $RepoTop ($p -replace '/', [IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath $local -PathType Leaf)) { continue }
    $size = (Get-Item -LiteralPath $local).Length
    $entries.Add((New-Entry $p $null ([int64]$size) $binary.Contains($p) $false 'work' $local))
  }
  return ,$entries.ToArray()
}

function Get-EntryText($Entry, [string]$RepoTop) {
  if ($Entry.Source -eq 'tree') {
    $r = Invoke-Git -Arguments @('-C', $RepoTop, 'cat-file', 'blob', $Entry.Blob)
    return $r.StdOut
  }
  return [IO.File]::ReadAllText($Entry.Local, [Text.UTF8Encoding]::new($false))
}

function Get-FrontmatterName([string]$Text) {
  $t = $Text -replace "`r`n", "`n"
  if (-not $t.StartsWith("---`n")) { return $null }
  $end = $t.IndexOf("`n---", 4)
  if ($end -lt 0) { return $null }
  $block = $t.Substring(4, $end - 4)
  foreach ($line in ($block -split "`n")) {
    if ($line -match '^name\s*:\s*(.+?)\s*$') { return $Matches[1].Trim().Trim('"').Trim("'") }
  }
  return $null
}

# ------------------------------------------------------------------------------------------------
# Tree rules
# ------------------------------------------------------------------------------------------------

# -RangeRules: the cheap per-file rules only (noise names, size cap, binaries, forbidden files,
# submodules, .gitattributes overrides), applied to every commit tree in a push range.
function Test-TreeRules {
  param(
    [Parameter(Mandatory)][object[]]$Entries,
    [Parameter(Mandatory)][string]$RepoTop,
    [string]$Label,
    [switch]$AdHoc,
    [switch]$RangeRules,
    [string]$VirtualPrefix = ''
  )
  # Rule 1: the top-level set (tips only, not in ad-hoc mode)
  if (-not $AdHoc -and -not $RangeRules) {
    $tops = @{}
    foreach ($e in $Entries) {
      $seg = $e.Path.Split('/')
      $top = $seg[0]
      $isDir = ($seg.Count -gt 1)
      if ($isDir) { if ($script:TopLevelDirs -cnotcontains $top) { $tops["dir:$top"] = $true } }
      else { if ($script:TopLevelFiles -cnotcontains $top) { $tops["file:$top"] = $true } }
    }
    foreach ($k in ($tops.Keys | Sort-Object)) { Add-Failure "${Label}: top-level entry not permitted: $(Format-Safe $k)" }
    if (-not ($Entries | Where-Object { $_.Path -eq $script:PatternConfigName })) { Add-Failure "${Label}: $($script:PatternConfigName) is missing from the tree" }
  }
  foreach ($e in $Entries) {
    $vpath = $VirtualPrefix + $e.Path
    $seg = $vpath.Split('/')
    $base = $seg[-1]
    if ($e.IsGitlink) { Add-Failure "${Label}: submodule entries are not permitted (their content is never scanned): $(Format-Safe $vpath)"; continue }
    # Rule 2: noise names (base names only) and noise directory names
    foreach ($g in $script:NoiseGlobs) { if ($base -like $g) { Add-Failure "${Label}: noise-shaped file name (matches $g): $(Format-Safe $vpath)"; break } }
    for ($i = 0; $i -lt $seg.Count - 1; $i++) { if ($script:NoiseDirs -contains $seg[$i].ToLowerInvariant()) { Add-Failure "${Label}: noise directory name ($($seg[$i])): $(Format-Safe $vpath)"; break } }
    # Rule 5: size cap
    if ($e.Size -gt $script:MaxFileBytes) { Add-Failure "${Label}: file over $($script:MaxFileBytes) bytes ($($e.Size)): $(Format-Safe $vpath)" }
    # Rule 6: binaries
    if ($e.IsBinary) { Add-Failure "${Label}: binary file: $(Format-Safe $vpath)" }
    # Forbidden files: a gitleaks ignore file anywhere; the pattern config anywhere but the root
    if ($base -ieq '.gitleaksignore') { Add-Failure "${Label}: .gitleaksignore is not permitted anywhere: $(Format-Safe $vpath)" }
    if ($base -ieq $script:PatternConfigName -and $vpath -ne $script:PatternConfigName) { Add-Failure "${Label}: $($script:PatternConfigName) is permitted at the root only: $(Format-Safe $vpath)" }
    # .gitattributes may not change how git tells text from binary, nor add filters
    if ($base -ieq '.gitattributes' -and -not $e.IsBinary -and $e.Size -le $script:MaxFileBytes) {
      $attrText = Get-EntryText $e $RepoTop
      $lineNo = 0
      foreach ($line in ($attrText -split "`n")) {
        $lineNo++
        $t = $line.TrimEnd("`r").Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $tokens = @($t -split '\s+')
        for ($k = 1; $k -lt $tokens.Count; $k++) {
          if ($tokens[$k] -match '^[-!]?diff(=|$)' -or $tokens[$k] -match '^[-!]?binary$' -or $tokens[$k] -match '^[-!]?filter(=|$)') {
            Add-Failure "${Label}: .gitattributes line $lineNo sets a diff, binary or filter attribute, which would change what the gate sees: $(Format-Safe $vpath)"
            break
          }
        }
      }
    }
  }
  if ($RangeRules) { return }
  # Rule 3: SKILL.md placement, frontmatter name, uniqueness
  $names = @{}
  foreach ($e in $Entries) {
    if ($e.IsGitlink) { continue }
    $vpath = $VirtualPrefix + $e.Path
    if ($vpath.Split('/')[-1] -ine 'SKILL.md') { continue }
    $m = [regex]::Match($vpath, '^(?:plugins/(aor-[a-z0-9-]+)/skills/([^/]+)|legacy/([^/]+))/SKILL\.md$')
    if (-not $m.Success) { Add-Failure "${Label}: SKILL.md outside plugins/aor-*/skills/<name>/ or legacy/<name>/: $(Format-Safe $vpath)"; continue }
    $dir = if ($m.Groups[2].Success) { $m.Groups[2].Value } else { $m.Groups[3].Value }
    $fm = Get-FrontmatterName (Get-EntryText $e $RepoTop)
    if ($null -eq $fm) { Add-Failure "${Label}: SKILL.md has no frontmatter name: $(Format-Safe $vpath)"; continue }
    if ($fm -cne $dir) { Add-Failure "${Label}: SKILL.md frontmatter name does not equal its directory name: $(Format-Safe $vpath)"; continue }
    if ($names.ContainsKey($dir)) { Add-Failure "${Label}: duplicate skill name across plugins and legacy: $(Format-Safe $dir)" } else { $names[$dir] = $vpath }
  }
  # Rule 4: plugin manifests and marketplace entries
  $plugins = @{}
  foreach ($e in $Entries) {
    $vpath = $VirtualPrefix + $e.Path
    if ($vpath -match '^plugins/([^/]+)/') { $plugins[$Matches[1]] = $true }
  }
  $marketplace = $null
  if (-not $AdHoc) {
    $mpEntry = $Entries | Where-Object { $_.Path -eq '.claude-plugin/marketplace.json' } | Select-Object -First 1
    if ($null -ne $mpEntry) {
      try { $marketplace = (Get-EntryText $mpEntry $RepoTop) | ConvertFrom-Json } catch { Add-Failure "${Label}: .claude-plugin/marketplace.json is not valid JSON" }
    }
  }
  foreach ($name in ($plugins.Keys | Sort-Object)) {
    $safeName = Format-Safe $name
    $manifestPath = "plugins/$name/.claude-plugin/plugin.json"
    $safeManifest = "plugins/$safeName/.claude-plugin/plugin.json"
    if ($name -cnotmatch '^aor-[a-z0-9-]+$') { Add-Failure "${Label}: plugin directory name must be aor-<family> (lowercase): $safeName" }
    $entry = $Entries | Where-Object { ($VirtualPrefix + $_.Path) -eq $manifestPath } | Select-Object -First 1
    if ($null -eq $entry) { Add-Failure "${Label}: missing $safeManifest"; continue }
    $json = $null
    try { $json = (Get-EntryText $entry $RepoTop) | ConvertFrom-Json } catch { Add-Failure "${Label}: $safeManifest is not valid JSON"; continue }
    $props = @($json.PSObject.Properties.Name)
    if ($props -notcontains 'name' -or [string]$json.name -cne $name) { Add-Failure "${Label}: $safeManifest name must equal the directory name" }
    if ($props -notcontains 'version' -or $null -eq (ConvertTo-SemVer ([string]$json.version))) { Add-Failure "${Label}: $safeManifest version must be a semver string" }
    $authorOk = $false
    if ($props -contains 'author') {
      $a = $json.author
      if ($a -is [string]) { $authorOk = -not [string]::IsNullOrWhiteSpace($a) }
      elseif ($null -ne $a -and @($a.PSObject.Properties.Name) -contains 'name') { $authorOk = -not [string]::IsNullOrWhiteSpace([string]$a.name) }
    }
    if (-not $authorOk) { Add-Failure "${Label}: $safeManifest must carry an author" }
    if ($props -notcontains 'license' -or [string]::IsNullOrWhiteSpace([string]$json.license)) { Add-Failure "${Label}: $safeManifest must carry a license" }
    if (-not $AdHoc) {
      if ($null -eq $marketplace) { Add-Failure "${Label}: .claude-plugin/marketplace.json is required when plugins/ exists" }
      else {
        $hits = 0
        $mpProps = @($marketplace.PSObject.Properties.Name)
        if ($mpProps -contains 'plugins') {
          foreach ($p in @($marketplace.plugins)) {
            $src = ''
            if ($null -ne $p -and @($p.PSObject.Properties.Name) -contains 'source') { $src = [string]$p.source }
            $src = $src -replace '\\', '/' -replace '^\./', '' -replace '/$', ''
            if ($src -ceq "plugins/$name") { $hits++ }
          }
        }
        if ($hits -ne 1) { Add-Failure "${Label}: marketplace must list plugins/$safeName exactly once (found $hits)" }
      }
    }
  }
  if (-not $AdHoc -and $null -ne $marketplace) {
    $mpProps = @($marketplace.PSObject.Properties.Name)
    if ($mpProps -contains 'plugins') {
      foreach ($p in @($marketplace.plugins)) {
        $src = ''
        if ($null -ne $p -and @($p.PSObject.Properties.Name) -contains 'source') { $src = [string]$p.source }
        $norm = $src -replace '\\', '/' -replace '^\./', '' -replace '/$', ''
        if ($norm -match '^plugins/([^/]+)$') { if (-not $plugins.ContainsKey($Matches[1])) { Add-Failure "${Label}: marketplace entry points at a plugin directory that does not exist: $(Format-Safe $norm)" } }
        else { Add-Failure "${Label}: marketplace entry source must be ./plugins/<name>: $(Format-Safe $src)" }
      }
    }
  }
  # Pattern-config allowlist shape and pinned count (from the tree being checked, or the script's own repo in ad-hoc mode)
  $cfgText = $null
  if ($AdHoc) { $cfgText = Get-AdHocPatternConfig }
  else {
    $cfgEntry = $Entries | Where-Object { $_.Path -eq $script:PatternConfigName } | Select-Object -First 1
    if ($null -ne $cfgEntry) { $cfgText = Get-EntryText $cfgEntry $RepoTop }
  }
  if ($null -ne $cfgText) {
    $shape = Test-AllowlistShape $cfgText
    foreach ($p in $shape.Problems) { Add-Failure "${Label}: $p" }
    if ($shape.Count -ne $script:PinnedAllowlistEntries) { Add-Failure "${Label}: $($script:PatternConfigName) holds $($shape.Count) allowlist entries; the gate pins $($script:PinnedAllowlistEntries)" }
    $ids = Get-RuleIds $cfgText
    $expected = @((Get-PatternSamples).Keys)
    $missing = @($expected | Where-Object { $ids -cnotcontains $_ })
    $extra = @($ids | Where-Object { $expected -cnotcontains $_ })
    if ($missing.Count -gt 0) { Add-Failure "${Label}: $($script:PatternConfigName) lacks rule(s) the gate expects: $($missing -join ', ')" }
    if ($extra.Count -gt 0) { Add-Failure "${Label}: $($script:PatternConfigName) carries rule(s) the gate has no control sample for: $(Format-Safe ($extra -join ', '))" }
  }
}

function Get-AdHocPatternConfig {
  $own = Join-Path (Split-Path -Parent $PSScriptRoot) $script:PatternConfigName
  if (-not (Test-Path -LiteralPath $own -PathType Leaf)) { throw "pattern config not found beside this script's repository root" }
  return [IO.File]::ReadAllText($own, [Text.UTF8Encoding]::new($false))
}

# ------------------------------------------------------------------------------------------------
# Literal layer
# ------------------------------------------------------------------------------------------------

function Split-GrepPrefix([string]$Record) {
  # "<sha>:<path>" when a tree was given; "<path>" otherwise.
  if ($Record.Length -gt 41 -and $Record[40] -eq ':' -and $Record.Substring(0, 40) -match '^[0-9a-f]{40}$') { return $Record.Substring(41) }
  return $Record
}

# Scans trees (or the working tree under -RelDir) with git grep -F. Reports path + line + count, never text.
# Every call uses -z so paths come back verbatim, never C-quoted.
function Invoke-LiteralContentScan {
  param([Parameter(Mandatory)][string]$RepoTop, [string[]]$Trees = @(), [string]$RelDir = $null, [string]$Label)
  $base = @('-C', $RepoTop, 'grep', '-i', '-F', '-f', '-', '-z')
  $scope = @()
  if ($Trees.Count -gt 0) { $scope += $Trees }
  $scope += '--'
  $scope += $(if ($RelDir) { $RelDir } else { '.' })
  $r = Invoke-Git -Arguments ($base + @('-l') + $scope) -StdinLines $script:Denylist -AllowedExit @(0, 1)
  if ($r.ExitCode -eq 1) { return 0 }
  $files = @($r.StdOut -split "`0" | ForEach-Object { $_.Trim("`n", "`r") } | Where-Object { $_ -ne '' })
  # Details: counts ("<key>\0<count>\n") and line numbers ("<key>\0<line>\0<content>\n") with the content field cut away.
  $counts = @{}
  $rc = Invoke-Git -Arguments ($base + @('-c') + $scope) -StdinLines $script:Denylist -AllowedExit @(0, 1)
  foreach ($rec in ($rc.StdOut -split "`n")) {
    $parts = $rec -split "`0"
    if ($parts.Count -ge 2 -and $parts[0] -ne '') { $counts[$parts[0]] = $parts[1].Trim() }
  }
  $lines = @{}
  $rn = Invoke-Git -Arguments ($base + @('-n') + $scope) -StdinLines $script:Denylist -AllowedExit @(0, 1)
  foreach ($rec in ($rn.StdOut -split "`n")) {
    if ($rec -eq '') { continue }
    $parts = $rec -split "`0"
    if ($parts.Count -ge 2 -and $parts[0] -ne '') {
      $key = $parts[0]
      if (-not $lines.ContainsKey($key)) { $lines[$key] = [System.Collections.Generic.List[string]]::new() }
      $lines[$key].Add($parts[1])
    }
  }
  foreach ($f in $files) {
    $path = Split-GrepPrefix $f
    $where = if ($f -ne $path -and $Trees.Count -gt 1) { " in $($f.Substring(0, 12))" } else { '' }
    $n = if ($counts.ContainsKey($f)) { $counts[$f] } else { '?' }
    $ln = if ($lines.ContainsKey($f)) { ($lines[$f] | Select-Object -Unique | Select-Object -First 20) -join ', ' } else { 'binary' }
    Add-Failure "${Label}: listed identifier in content$where`: $(Format-Safe $path) (lines $ln; $n match line(s)); re-derive locally with git grep -n"
  }
  return $files.Count
}

function Invoke-LiteralNameScan([string[]]$Names, [string]$Label) {
  $hits = 0
  foreach ($n in $Names) { if (Test-ContainsListed $n) { $hits++ } }
  if ($hits -gt 0) { Add-Failure "${Label}: listed identifier in $hits file name(s); the names are withheld; re-derive locally with git ls-files" }
  return $hits
}

# Author, committer and message of every commit, as one text block per commit (sha first).
function Get-CommitMetadata([string]$RepoTop, [string[]]$Commits) {
  $out = [System.Collections.Generic.List[object]]::new()
  if ($Commits.Count -eq 0) { return ,$out.ToArray() }
  foreach ($batch in (Split-Batches $Commits 200)) {
    $r = Invoke-Git -Arguments (@('-C', $RepoTop, 'log', '--no-walk', '--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x00%x01') + $batch)
    foreach ($rec in ($r.StdOut -split "`u{0001}")) {
      $parts = $rec -split "`0"
      if ($parts.Count -lt 6) { continue }
      $sha = $parts[0].Trim()
      if ($sha -eq '') { continue }
      $out.Add([pscustomobject]@{ Sha = $sha; Text = ($parts[1..5] -join "`n") })
    }
  }
  return ,$out.ToArray()
}

function Invoke-LiteralMetadataScan([object[]]$Metadata, [string]$Label) {
  $hits = 0
  foreach ($m in $Metadata) {
    if (Test-ContainsListed $m.Text) { $hits++; Add-Failure "${Label}: listed identifier in the author, committer or message of commit $($m.Sha.Substring(0, 12))" }
  }
  return $hits
}

# An annotated tag is its own object with a tagger and a message; peel through nested tags.
function Get-TagObjectTexts([string]$RepoTop, [string]$Sha) {
  $texts = [System.Collections.Generic.List[string]]::new()
  $current = $Sha
  for ($depth = 0; $depth -lt 8; $depth++) {
    $t = Invoke-Native -Exe 'git' -Arguments @('-C', $RepoTop, 'cat-file', '-t', $current)
    if ($t.ExitCode -ne 0 -or $t.StdOut.Trim() -ne 'tag') { break }
    $body = (Invoke-Git -Arguments @('-C', $RepoTop, 'cat-file', '-p', $current)).StdOut
    $texts.Add($body)
    $next = $null
    foreach ($line in ($body -split "`n")) { if ($line -match '^object ([0-9a-f]{40})') { $next = $Matches[1]; break } }
    if ($null -eq $next) { break }
    $current = $next
  }
  return ,$texts.ToArray()
}

function Split-Batches([string[]]$Items, [int]$Size) {
  $out = [System.Collections.Generic.List[object]]::new()
  for ($i = 0; $i -lt $Items.Count; $i += $Size) { $out.Add(@($Items[$i..([Math]::Min($i + $Size, $Items.Count) - 1)])) }
  return ,$out.ToArray()
}

# ------------------------------------------------------------------------------------------------
# Positive control
# ------------------------------------------------------------------------------------------------

function Invoke-PositiveControl([string]$GitleaksExe, [string]$ConfigA, [string]$ConfigB) {
  $ok = $true
  if ($null -ne $script:Denylist) {
    $first = $script:Denylist[0]
    $blob = ($script:ControlWrapper -f $first) + "`n"
    $dir = [IO.Path]::GetTempPath()
    $name = 'aor-gate-control-' + [guid]::NewGuid().ToString('n') + '.txt'
    $file = Join-Path $dir $name
    try {
      [IO.File]::WriteAllText($file, $blob, [Text.UTF8Encoding]::new($false))
      $envMap = @{ GIT_DIR = $null; GIT_INDEX_FILE = $null; GIT_WORK_TREE = $null }
      $r = Invoke-Native -Exe 'git' -Arguments @('grep', '-l', '-i', '-F', '-f', '-', '--no-index', '--', $name) -StdinLines $script:Denylist -WorkingDirectory $dir -Environment $envMap
      if ($r.ExitCode -ne 0) { $ok = $false; Add-Failure "positive control failed: the literal layer did not detect its own sample (git grep exit $($r.ExitCode))" }
    } finally { if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force } }
    if (-not (Test-ContainsListed $blob)) { $ok = $false; Add-Failure 'positive control failed: the name and metadata check did not detect its own sample' }
  }
  if (-not [string]::IsNullOrEmpty($GitleaksExe)) {
    $samples = Get-PatternSamples
    $blob = (($samples.Values | ForEach-Object { "line $_ end" }) -join "`n") + "`n"
    foreach ($pass in @(@{ Name = 'default+own'; Toml = $ConfigA }, @{ Name = 'own-only'; Toml = $ConfigB })) {
      try {
        $findings = Invoke-Gitleaks -Exe $GitleaksExe -Arguments @('stdin') -ConfigToml $pass.Toml -StdinText $blob
        $found = @($findings | ForEach-Object { $_.RuleID } | Select-Object -Unique)
        $missing = @($samples.Keys | Where-Object { $found -cnotcontains $_ })
        if ($missing.Count -gt 0) { $ok = $false; Add-Failure "positive control failed: pattern pass $($pass.Name) did not detect: $($missing -join ', ')" }
      } catch { $ok = $false; Add-Failure "positive control failed: pattern pass $($pass.Name) error: $($_.Exception.GetType().Name): $(Format-Safe $_.Exception.Message)" }
    }
  }
  if ($ok) { Write-Gate 'positive control passed' }
  return $ok
}

# ------------------------------------------------------------------------------------------------
# Pattern layer
# ------------------------------------------------------------------------------------------------

function Report-Findings($Findings, [string]$Label, [string]$Pass) {
  foreach ($f in $Findings) {
    $props = @($f.PSObject.Properties.Name)
    $file = if ($props -contains 'File') { [string]$f.File } else { '' }
    $line = if ($props -contains 'StartLine') { [string]$f.StartLine } else { '?' }
    $commit = if ($props -contains 'Commit' -and -not [string]::IsNullOrEmpty([string]$f.Commit)) { " in $(([string]$f.Commit).Substring(0, 12))" } else { '' }
    Add-Failure "${Label}: pattern $($f.RuleID) ($Pass)$commit`: $(Format-Safe $file) line $line"
  }
}

function Invoke-PatternScan {
  param(
    [Parameter(Mandatory)][string]$GitleaksExe,
    [Parameter(Mandatory)][string]$RepoTop,
    [Parameter(Mandatory)][string]$ConfigA,
    [Parameter(Mandatory)][string]$ConfigB,
    [Parameter(Mandatory)][string]$Label,
    [string[]]$GitArguments = $null,
    [string]$Directory = $null
  )
  foreach ($pass in @(@{ Name = 'default+own'; Toml = $ConfigA }, @{ Name = 'own-only'; Toml = $ConfigB })) {
    $glArgs = if (-not [string]::IsNullOrEmpty($Directory)) { @('dir', $Directory) } else { @('git') + $GitArguments + @('.') }
    try {
      $findings = Invoke-Gitleaks -Exe $GitleaksExe -Arguments $glArgs -ConfigToml $pass.Toml -WorkingDirectory $RepoTop
      Report-Findings $findings $Label $pass.Name
    } catch { Add-Failure "${Label}: pattern pass $($pass.Name) error: $($_.Exception.GetType().Name): $(Format-Safe $_.Exception.Message)" }
  }
}

# File names, commit metadata and tag objects through the pattern rules (stdin, both passes).
function Invoke-PatternTextScan {
  param(
    [Parameter(Mandatory)][string]$GitleaksExe,
    [Parameter(Mandatory)][string]$ConfigA,
    [Parameter(Mandatory)][string]$ConfigB,
    [Parameter(Mandatory)][string]$Label,
    [string[]]$Texts = @(),
    [string]$What = 'file names, commit metadata and tag objects'
  )
  $blob = (($Texts | Where-Object { -not [string]::IsNullOrEmpty($_) }) -join "`n") + "`n"
  if ($blob.Trim() -eq '') { return }
  foreach ($pass in @(@{ Name = 'default+own'; Toml = $ConfigA }, @{ Name = 'own-only'; Toml = $ConfigB })) {
    try {
      $findings = Invoke-Gitleaks -Exe $GitleaksExe -Arguments @('stdin') -ConfigToml $pass.Toml -StdinText $blob
      foreach ($f in $findings) { Add-Failure "${Label}: pattern $($f.RuleID) ($($pass.Name)) in $What (line $([string]$f.StartLine) of that text); re-derive locally" }
    } catch { Add-Failure "${Label}: pattern pass $($pass.Name) over $What error: $($_.Exception.GetType().Name): $(Format-Safe $_.Exception.Message)" }
  }
}

# ------------------------------------------------------------------------------------------------
# Version bump and tags
# ------------------------------------------------------------------------------------------------

function Get-PluginVersionAt([string]$RepoTop, [string]$Commit, [string]$Plugin) {
  $spec = "${Commit}:plugins/$Plugin/.claude-plugin/plugin.json"
  $r = Invoke-Native -Exe 'git' -Arguments @('-C', $RepoTop, 'cat-file', '-e', $spec)
  if ($r.ExitCode -ne 0) { return $null }
  $j = (Invoke-Git -Arguments @('-C', $RepoTop, 'cat-file', 'blob', $spec)).StdOut | ConvertFrom-Json
  if (@($j.PSObject.Properties.Name) -notcontains 'version') { return '' }
  return [string]$j.version
}

function Test-VersionBump([string]$RepoTop, [string]$Base, [string]$Tip, [string]$Label) {
  $touched = Get-GitLines @('-C', $RepoTop, 'diff', '--name-only', $Base, $Tip, '--', 'plugins/')
  $plugins = @($touched | ForEach-Object { if ($_ -match '^plugins/([^/]+)/') { $Matches[1] } } | Select-Object -Unique)
  if ($plugins.Count -eq 0) { Write-Gate "${Label}: no plugin touched; version check not applicable"; return }
  foreach ($p in $plugins) {
    $safe = Format-Safe "plugins/$p"
    $tipV = Get-PluginVersionAt $RepoTop $Tip $p
    $baseV = Get-PluginVersionAt $RepoTop $Base $p
    if ($null -eq $tipV) { Write-Gate "${Label}: $safe removed at the tip; version check not applicable"; continue }
    $tipS = ConvertTo-SemVer $tipV
    if ($null -eq $tipS) { Add-Failure "${Label}: $safe version at the tip is not semver"; continue }
    if ($null -eq $baseV) { Write-Gate "${Label}: $safe is new at $(Format-Safe $tipV)"; continue }
    $baseS = ConvertTo-SemVer $baseV
    if ($null -eq $baseS) { Write-Gate "${Label}: $safe had no semver version at the base; $(Format-Safe $tipV) accepted"; continue }
    if ((Compare-SemVer $tipS $baseS) -le 0) { Add-Failure "${Label}: $safe changed but its version did not increase ($(Format-Safe $baseV) to $(Format-Safe $tipV))" }
    else { Write-Gate "${Label}: $safe $(Format-Safe $baseV) to $(Format-Safe $tipV)" }
  }
}

function Test-TagRules([string]$RepoTop, [string]$TagName, [string]$Sha, [string]$MainRef, [string]$Label) {
  $safeTag = Format-Safe $TagName
  $m = [regex]::Match($TagName, '^([a-z0-9][a-z0-9-]*)--v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$')
  if (-not $m.Success) { Add-Failure "${Label}: tag name must be <plugin>--v<version>: $safeTag"; return }
  $plugin = $m.Groups[1].Value; $ver = $m.Groups[2].Value
  $r = Invoke-Native -Exe 'git' -Arguments @('-C', $RepoTop, 'rev-parse', '--verify', '--quiet', "$Sha^{commit}")
  if ($r.ExitCode -ne 0) { Add-Failure "${Label}: tag $safeTag does not point at a commit"; return }
  $commit = $r.StdOut.Trim()
  $v = Get-PluginVersionAt $RepoTop $commit $plugin
  if ($null -eq $v) { Add-Failure "${Label}: tag $safeTag names a plugin that has no manifest at the tagged commit"; return }
  if ($v -cne $ver) { Add-Failure "${Label}: tag $safeTag does not match the plugin version at the tagged commit ($(Format-Safe $v))" }
  $anc = Invoke-Native -Exe 'git' -Arguments @('-C', $RepoTop, 'merge-base', '--is-ancestor', $commit, $MainRef)
  if ($anc.ExitCode -ne 0) { Add-Failure "${Label}: tag $safeTag points at a commit not reachable from $MainRef" }
}

function Invoke-Validators([string]$RepoTop, [object[]]$TipEntries, [string]$Label) {
  $r = Invoke-Native -Exe 'claude' -Arguments @('plugin', 'validate', '.') -WorkingDirectory $RepoTop
  if ($r.ExitCode -ne 0) { Add-Failure "${Label}: claude plugin validate exited $($r.ExitCode); re-run it locally for the reason" } else { Write-Gate "${Label}: claude plugin validate passed" }
  $hasPluginSkills = @($TipEntries | Where-Object { $_.Path -match '^plugins/[^/]+/skills/[^/]+/SKILL\.md$' }).Count -gt 0
  if ($hasPluginSkills) {
    $g = Invoke-Native -Exe 'gh' -Arguments @('skill', 'publish', '--dry-run') -WorkingDirectory $RepoTop
    if ($g.ExitCode -ne 0) { Add-Failure "${Label}: gh skill publish --dry-run exited $($g.ExitCode); re-run it locally for the reason" } else { Write-Gate "${Label}: gh skill publish --dry-run passed" }
  } else { Write-Gate "${Label}: no plugin skills in the tree; gh skill publish --dry-run skipped" }
}

# ------------------------------------------------------------------------------------------------
# Placements
# ------------------------------------------------------------------------------------------------

function Get-RepoTop([string]$Start) {
  $r = Invoke-Git -Arguments @('-C', $Start, 'rev-parse', '--show-toplevel')
  return $r.StdOut.Trim()
}

function Get-TreeText([string]$RepoTop, [string]$Tree, [string]$RelPath) {
  $r = Invoke-Native -Exe 'git' -Arguments @('-C', $RepoTop, 'cat-file', 'blob', "${Tree}:$RelPath")
  if ($r.ExitCode -ne 0) { return $null }
  return $r.StdOut
}

function Initialize-Gitleaks([string]$RepoTop, [bool]$FromEnvironment) {
  $exe = $null
  if ($FromEnvironment) { $exe = $env:AOR_GITLEAKS }
  else {
    $r = Invoke-Native -Exe 'git' -Arguments @('-C', $RepoTop, 'config', '--local', '--get', 'aor.gitleaks')
    if ($r.ExitCode -eq 0) { $exe = $r.StdOut.Trim() }
  }
  if ([string]::IsNullOrWhiteSpace($exe)) { Add-Failure 'scanner not configured (aor.gitleaks / AOR_GITLEAKS); run scripts/install-gate.ps1'; return $null }
  if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { Add-Failure 'scanner binary missing at the configured path'; return $null }
  if (-not (Test-GitleaksPinned $exe)) { Add-Failure "scanner hash mismatch: the binary at the configured path is not gitleaks $($script:GitleaksVersion) as pinned"; return $null }
  Write-Gate "gitleaks $($script:GitleaksVersion) verified by hash"
  return $exe
}

# Runs every layer over one set of targets.
function Invoke-Layers {
  param(
    [Parameter(Mandatory)][string]$RepoTop,
    [string]$ConfigA,
    [string]$ConfigB,
    [string]$GitleaksExe,
    [string[]]$Commits = @(),
    [string[]]$GrepTrees = @(),
    [object[]]$TipTrees = @(),
    [string[]]$RangeCommits = @(),
    [string[]]$Names = @(),
    [string[]]$TagTexts = @(),
    [string[]]$PatternGitArguments = $null,
    [bool]$ScanAllHistoryPatterns = $false,
    [string]$Label = 'scan'
  )
  # Tree rules on every tip tree
  $tipTreeShas = [System.Collections.Generic.HashSet[string]]::new()
  foreach ($t in $TipTrees) {
    [void]$tipTreeShas.Add([string]$t.Tree)
    try {
      $entries = Get-TreeEntries $t.Tree $RepoTop
      Test-TreeRules -Entries $entries -RepoTop $RepoTop -Label $t.Label
      $t['Entries'] = $entries
    } catch { Add-Failure "$($t.Label): tree rules error: $($_.Exception.GetType().Name): $(Format-Safe $_.Exception.Message)" }
  }
  # The per-file rules on every other commit tree in the range
  foreach ($c in $RangeCommits) {
    try {
      $tree = (Invoke-Git -Arguments @('-C', $RepoTop, 'rev-parse', '--verify', "$c^{tree}")).StdOut.Trim()
      if ($tipTreeShas.Contains($tree)) { continue }
      [void]$tipTreeShas.Add($tree)
      $entries = Get-TreeEntries $tree $RepoTop
      Test-TreeRules -Entries $entries -RepoTop $RepoTop -Label "commit $($c.Substring(0, 12)) tree" -RangeRules
    } catch { Add-Failure "commit $($c.Substring(0, 12)) tree: tree rules error: $($_.Exception.GetType().Name): $(Format-Safe $_.Exception.Message)" }
  }
  $metadata = @()
  try { $metadata = Get-CommitMetadata $RepoTop $Commits } catch { Add-Failure "${Label}: commit metadata error: $($_.Exception.GetType().Name): $(Format-Safe $_.Exception.Message)" }
  # Literal layer
  if ($null -ne $script:Denylist) {
    try {
      $c = 0
      if ($GrepTrees.Count -gt 0) { foreach ($batch in (Split-Batches $GrepTrees 100)) { $c += Invoke-LiteralContentScan -RepoTop $RepoTop -Trees $batch -Label $Label } }
      $n = Invoke-LiteralNameScan -Names $Names -Label $Label
      $m = Invoke-LiteralMetadataScan -Metadata $metadata -Label $Label
      $tg = 0
      foreach ($tt in $TagTexts) { if (Test-ContainsListed $tt) { $tg++ } }
      if ($tg -gt 0) { Add-Failure "${Label}: listed identifier in the tagger or message of $tg tag object(s)" }
      if (($c + $n + $m + $tg) -eq 0) { Write-Gate "${Label}: literal layer clean" }
    } catch { Add-Failure "${Label}: literal layer error: $($_.Exception.GetType().Name): $(Format-Safe $_.Exception.Message)" }
  } else { Write-Gate "${Label}: literal layer skipped (denylist not loaded)" }
  # Pattern layer
  if (-not [string]::IsNullOrEmpty($GitleaksExe)) {
    $before = $script:Failures.Count
    if ($ScanAllHistoryPatterns) { Invoke-PatternScan -GitleaksExe $GitleaksExe -RepoTop $RepoTop -ConfigA $ConfigA -ConfigB $ConfigB -Label $Label -GitArguments @() }
    elseif ($null -ne $PatternGitArguments) { Invoke-PatternScan -GitleaksExe $GitleaksExe -RepoTop $RepoTop -ConfigA $ConfigA -ConfigB $ConfigB -Label $Label -GitArguments $PatternGitArguments }
    elseif ($Commits.Count -gt 0) {
      foreach ($batch in (Split-Batches $Commits 200)) {
        Invoke-PatternScan -GitleaksExe $GitleaksExe -RepoTop $RepoTop -ConfigA $ConfigA -ConfigB $ConfigB -Label $Label -GitArguments @('--log-opts', ('--no-walk ' + ($batch -join ' ')))
      }
    }
    $texts = @($Names) + @($metadata | ForEach-Object { $_.Text }) + @($TagTexts)
    Invoke-PatternTextScan -GitleaksExe $GitleaksExe -ConfigA $ConfigA -ConfigB $ConfigB -Label $Label -Texts $texts
    if ($script:Failures.Count -eq $before) { Write-Gate "${Label}: pattern layer clean" }
  } else { Write-Gate "${Label}: pattern layer skipped (scanner not verified)" }
}

function Get-ConfigsFromTree([string]$RepoTop, [string]$Tree) {
  $a = Get-TreeText $RepoTop $Tree $script:PatternConfigName
  if ($null -eq $a) { return $null }
  return @{ A = $a; B = (ConvertTo-OwnOnlyConfig $a) }
}

function Get-TreeNames([string]$RepoTop, [string]$Tree) {
  return Get-GitNulSeparated @('-C', $RepoTop, 'ls-tree', '-r', '-z', '--name-only', $Tree)
}

function Invoke-PreCommit {
  $top = Get-RepoTop '.'
  Write-Gate 'placement: pre-commit'
  $status = Initialize-Denylist $top $false; Write-Gate $status
  if ($null -eq $script:Denylist) { Add-Failure $status }
  $exe = Initialize-Gitleaks $top $false
  $tree = (Invoke-Git -Arguments @('-C', $top, 'write-tree')).StdOut.Trim()
  $cfg = Get-ConfigsFromTree $top $tree
  if ($null -eq $cfg) { Add-Failure "$($script:PatternConfigName) is missing from the staged tree"; $exe = $null; $cfg = @{ A = ''; B = '' } }
  if ($null -ne $exe -or $null -ne $script:Denylist) { [void](Invoke-PositiveControl $exe $cfg.A $cfg.B) }
  $names = Get-TreeNames $top $tree
  Invoke-Layers -RepoTop $top -ConfigA $cfg.A -ConfigB $cfg.B -GitleaksExe $exe -GrepTrees @($tree) -TipTrees @(@{ Tree = $tree; Label = 'staged tree' }) -Names $names -PatternGitArguments @('--pre-commit', '--staged') -Label 'staged'
}

function Invoke-PrePush {
  $top = Get-RepoTop '.'
  Write-Gate "placement: pre-push to $(Format-Safe $Remote)"
  if ($script:IncidentMode) { Write-Gate 'INCIDENT MODE: a non-fast-forward push to main and a tag deletion are permitted for this push; everything is still scanned' }
  $status = Initialize-Denylist $top $false; Write-Gate $status
  if ($null -eq $script:Denylist) { Add-Failure $status }
  $exe = Initialize-Gitleaks $top $false
  $stdin = [Console]::In.ReadToEnd()
  $refLines = @($stdin -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
  if ($refLines.Count -eq 0) { Write-Gate 'no refs to push'; return }
  $allCommits = [System.Collections.Generic.List[string]]::new()
  $tips = [System.Collections.Generic.List[object]]::new()
  $versionChecks = [System.Collections.Generic.List[object]]::new()
  $tagChecks = [System.Collections.Generic.List[object]]::new()
  $tagTexts = [System.Collections.Generic.List[string]]::new()
  $touchesPlugins = $false
  $configTree = $null
  foreach ($line in $refLines) {
    $parts = $line -split '\s+'
    if ($parts.Count -lt 4) { Add-Failure "malformed pre-push line"; continue }
    $localRef = $parts[0]; $localSha = $parts[1]; $remoteRef = $parts[2]; $remoteSha = $parts[3]
    $isTag = $remoteRef.StartsWith('refs/tags/')
    $isMain = ($remoteRef -eq 'refs/heads/main')
    $label = "ref $(Format-Safe $remoteRef)"
    if ($localSha -match '^0+$') {
      if ($isTag) { if ($script:IncidentMode) { Write-Gate "${label}: tag deletion permitted in INCIDENT MODE" } else { Add-Failure "${label}: tag deletion is refused outside INCIDENT MODE" } }
      elseif ($isMain) { Add-Failure "${label}: deleting main is refused" }
      else { Write-Gate "${label}: branch deletion; nothing to scan" }
      continue
    }
    $tipCommit = (Invoke-Git -Arguments @('-C', $top, 'rev-parse', '--verify', "$localSha^{commit}")).StdOut.Trim()
    $tipTree = (Invoke-Git -Arguments @('-C', $top, 'rev-parse', '--verify', "$tipCommit^{tree}")).StdOut.Trim()
    if ($null -eq $configTree) { $configTree = $tipTree }
    if ($isTag) { foreach ($tt in (Get-TagObjectTexts $top $localSha)) { $tagTexts.Add($tt) } }
    $range = $null
    $base = $null
    if ($remoteSha -match '^0+$') {
      $range = @($localSha, '--not', "--remotes=$Remote")
      Write-Gate "${label}: new on the remote; scanning every commit not already on $(Format-Safe $Remote)"
    } else {
      $known = (Invoke-Native -Exe 'git' -Arguments @('-C', $top, 'cat-file', '-e', "$remoteSha^{commit}")).ExitCode -eq 0
      if (-not $known) {
        if ($script:IncidentMode) { $range = @($localSha, '--not', "--remotes=$Remote"); Write-Gate "${label}: remote tip unknown locally; INCIDENT MODE scans every commit not already on the remote; version check skipped" }
        else { Add-Failure "${label}: the remote tip is not known locally; fetch first"; continue }
      } else {
        $ff = (Invoke-Native -Exe 'git' -Arguments @('-C', $top, 'merge-base', '--is-ancestor', $remoteSha, $localSha)).ExitCode -eq 0
        if (-not $ff -and $isMain -and -not $script:IncidentMode) { Add-Failure "${label}: non-fast-forward push to main is refused outside INCIDENT MODE"; continue }
        if (-not $ff) { Write-Gate "${label}: non-fast-forward ($(if ($isMain) { 'INCIDENT MODE' } else { 'not main' })); scanning the whole new range; version check skipped (no fast-forward base)" }
        $range = @("$remoteSha..$localSha")
        if ($ff) { $base = $remoteSha }
      }
    }
    $commits = Get-GitLines (@('-C', $top, 'rev-list') + $range)
    if ($commits.Count -eq 0) { Write-Gate "${label}: empty range" } else { Write-Gate "${label}: $($commits.Count) commit(s) in range" }
    foreach ($c in $commits) { if (-not $allCommits.Contains($c)) { $allCommits.Add($c) } }
    $tips.Add(@{ Tree = $tipTree; Label = "$label tip" })
    if ($isTag) { $tagChecks.Add(@{ Name = $remoteRef.Substring(10); Sha = $localSha; Label = $label }) }
    if ($isMain -and $null -ne $base) { $versionChecks.Add(@{ Base = $base; Tip = $tipCommit; Label = $label }) }
    if ($null -ne $base) {
      $touched = Get-GitLines @('-C', $top, 'diff', '--name-only', $base, $tipCommit, '--', 'plugins/', '.claude-plugin/')
      if ($touched.Count -gt 0) { $touchesPlugins = $true }
    } elseif ($commits.Count -gt 0) {
      $touched = Get-GitLines (@('-C', $top, 'log', '--format=', '--name-only', '--no-walk') + $commits + @('--', 'plugins/', '.claude-plugin/'))
      if ($touched.Count -gt 0) { $touchesPlugins = $true }
    }
  }
  if ($tips.Count -eq 0 -and $allCommits.Count -eq 0 -and $tagTexts.Count -eq 0) { Write-Gate 'nothing to scan for this push'; return }
  $cfg = $null
  if ($null -ne $configTree) { $cfg = Get-ConfigsFromTree $top $configTree }
  if ($null -eq $cfg) { if ($tips.Count -gt 0) { Add-Failure "$($script:PatternConfigName) is missing from the pushed tree" }; $exe = $null; $cfg = @{ A = ''; B = '' } }
  if ($null -ne $exe -or $null -ne $script:Denylist) { [void](Invoke-PositiveControl $exe $cfg.A $cfg.B) }
  $names = [System.Collections.Generic.HashSet[string]]::new()
  foreach ($c in $allCommits) { foreach ($n in (Get-TreeNames $top $c)) { [void]$names.Add($n) } }
  Invoke-Layers -RepoTop $top -ConfigA $cfg.A -ConfigB $cfg.B -GitleaksExe $exe -Commits $allCommits.ToArray() -GrepTrees $allCommits.ToArray() -TipTrees $tips.ToArray() -RangeCommits $allCommits.ToArray() -Names @($names) -TagTexts $tagTexts.ToArray() -Label 'push'
  foreach ($v in $versionChecks) { Test-VersionBump $top $v.Base $v.Tip $v.Label }
  foreach ($t in $tagChecks) { Test-TagRules $top $t.Name $t.Sha 'main' $t.Label }
  if ($touchesPlugins) {
    $tipEntries = @()
    if ($tips.Count -gt 0 -and $tips[0].ContainsKey('Entries')) { $tipEntries = $tips[0]['Entries'] }
    Invoke-Validators $top $tipEntries 'push'
  } else { Write-Gate 'push: plugins/ not touched; validators skipped' }
}

function Invoke-CiMode {
  $top = if ($env:GITHUB_WORKSPACE) { Get-RepoTop $env:GITHUB_WORKSPACE } else { Get-RepoTop '.' }
  $eventName = [string]$env:GITHUB_EVENT_NAME
  $ref = [string]$env:GITHUB_REF
  $sha = [string]$env:GITHUB_SHA
  Write-Gate "placement: ci ($eventName on $(Format-Safe $ref))"
  $status = Initialize-Denylist $top $true; Write-Gate $status
  if ($null -eq $script:Denylist) { Add-Failure $status }
  $exe = Initialize-Gitleaks $top $true
  $payload = $null
  if ($env:GITHUB_EVENT_PATH -and (Test-Path -LiteralPath $env:GITHUB_EVENT_PATH)) { try { $payload = Get-Content -LiteralPath $env:GITHUB_EVENT_PATH -Raw | ConvertFrom-Json } catch { $payload = $null } }
  $tipCommit = (Invoke-Git -Arguments @('-C', $top, 'rev-parse', '--verify', 'HEAD^{commit}')).StdOut.Trim()
  $tipTree = (Invoke-Git -Arguments @('-C', $top, 'rev-parse', '--verify', 'HEAD^{tree}')).StdOut.Trim()
  $cfg = Get-ConfigsFromTree $top $tipTree
  if ($null -eq $cfg) { Add-Failure "$($script:PatternConfigName) is missing from the checked-out tree"; $exe = $null; $cfg = @{ A = ''; B = '' } }
  if ($null -ne $exe -or $null -ne $script:Denylist) { [void](Invoke-PositiveControl $exe $cfg.A $cfg.B) }
  $base = $null
  $rangeUnavailable = $true
  $reason = ''
  if ($eventName -eq 'pull_request') {
    $prBase = ''
    if ($null -ne $payload -and @($payload.PSObject.Properties.Name) -contains 'pull_request') { $prBase = [string]$payload.pull_request.base.sha }
    if ($prBase -ne '' -and (Invoke-Native -Exe 'git' -Arguments @('-C', $top, 'cat-file', '-e', "$prBase^{commit}")).ExitCode -eq 0) { $base = $prBase; $rangeUnavailable = $false } else { $reason = 'pull request base not reachable' }
  } elseif ($eventName -eq 'push') {
    $before = ''; $created = $false; $forced = $false
    if ($null -ne $payload) {
      $pp = @($payload.PSObject.Properties.Name)
      if ($pp -contains 'before') { $before = [string]$payload.before }
      if ($pp -contains 'created') { $created = [bool]$payload.created }
      if ($pp -contains 'forced') { $forced = [bool]$payload.forced }
    }
    if ($created) { $reason = 'ref created' }
    elseif ($forced) { $reason = 'ref rewritten (forced)' }
    elseif ($before -eq '' -or $before -match '^0+$') { $reason = 'no before sha' }
    elseif ((Invoke-Native -Exe 'git' -Arguments @('-C', $top, 'cat-file', '-e', "$before^{commit}")).ExitCode -ne 0) { $reason = 'before sha not reachable' }
    elseif ((Invoke-Native -Exe 'git' -Arguments @('-C', $top, 'merge-base', '--is-ancestor', $before, $tipCommit)).ExitCode -ne 0) { $reason = 'before sha is not an ancestor of HEAD' }
    else { $base = $before; $rangeUnavailable = $false }
  } else { $reason = "event $eventName carries no range" }
  $commits = @()
  $names = [System.Collections.Generic.HashSet[string]]::new()
  $rangeCommits = @()
  if ($rangeUnavailable) {
    Write-Gate "range unavailable ($reason): version check skipped; every commit reachable from HEAD is scanned; tree rules on the HEAD tree"
    $commits = Get-GitLines @('-C', $top, 'rev-list', 'HEAD')
    foreach ($n in (Get-TreeNames $top $tipTree)) { [void]$names.Add($n) }
    foreach ($n in (Get-GitLines @('-C', $top, 'log', '--format=', '--name-only', 'HEAD'))) { [void]$names.Add($n) }
  } else {
    $commits = Get-GitLines @('-C', $top, 'rev-list', "$base..HEAD")
    Write-Gate "range $($base.Substring(0, 12))..HEAD: $($commits.Count) commit(s)"
    foreach ($c in $commits) { foreach ($n in (Get-TreeNames $top $c)) { [void]$names.Add($n) } }
    $rangeCommits = $commits
  }
  $tagTexts = @()
  if ($eventName -eq 'push' -and $ref.StartsWith('refs/tags/')) {
    $tagRef = Invoke-Native -Exe 'git' -Arguments @('-C', $top, 'rev-parse', '--verify', '--quiet', $ref)
    if ($tagRef.ExitCode -eq 0) { $tagTexts = Get-TagObjectTexts $top $tagRef.StdOut.Trim() }
  }
  $tips = @(@{ Tree = $tipTree; Label = 'HEAD tree' })
  Invoke-Layers -RepoTop $top -ConfigA $cfg.A -ConfigB $cfg.B -GitleaksExe $exe -Commits $commits -GrepTrees $commits -TipTrees $tips -RangeCommits $rangeCommits -Names @($names) -TagTexts $tagTexts -ScanAllHistoryPatterns $rangeUnavailable -Label 'ci'
  $isMainPush = ($eventName -eq 'push' -and $ref -eq 'refs/heads/main')
  $isPrToMain = ($eventName -eq 'pull_request' -and [string]$env:GITHUB_BASE_REF -eq 'main')
  if (-not $rangeUnavailable -and ($isMainPush -or $isPrToMain)) { Test-VersionBump $top $base $tipCommit 'ci' }
  if ($eventName -eq 'push' -and $ref.StartsWith('refs/tags/')) {
    $mainRef = 'refs/remotes/origin/main'
    if ((Invoke-Native -Exe 'git' -Arguments @('-C', $top, 'rev-parse', '--verify', '--quiet', $mainRef)).ExitCode -ne 0) { $mainRef = 'main' }
    Test-TagRules $top $ref.Substring(10) $sha $mainRef 'ci'
  }
}

function Invoke-AdHoc {
  $target = (Resolve-Path -LiteralPath $Path).Path
  if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw "-Path must name a directory" }
  $top = Get-RepoTop $target
  $rel = [IO.Path]::GetRelativePath($top, $target) -replace '\\', '/'
  if ($rel -eq '.' -or $rel.StartsWith('..')) { throw "-Path must be a directory inside a git work tree, below its root" }
  $basename = Split-Path -Leaf $target
  $status = Initialize-Denylist $top $false
  Write-Gate "placement: ad-hoc on $(Format-Safe $rel) as plugins/$(Format-Safe $basename)/"
  Write-Gate $status
  if ($null -eq $script:Denylist) { Add-Failure $status }
  $exe = Initialize-Gitleaks $top $false
  $a = Get-AdHocPatternConfig
  $cfg = @{ A = $a; B = (ConvertTo-OwnOnlyConfig $a) }
  if ($null -ne $exe -or $null -ne $script:Denylist) { [void](Invoke-PositiveControl $exe $cfg.A $cfg.B) }
  $entries = Get-WorkEntries $top $rel
  $prefix = "plugins/$basename/"
  # Re-base entries so that the virtual prefix replaces the directory's own path
  $rebased = @($entries | ForEach-Object { New-Entry ($_.Path.Substring($rel.Length).TrimStart('/')) $null $_.Size $_.IsBinary $_.IsGitlink 'work' $_.Local })
  Test-TreeRules -Entries $rebased -RepoTop $top -Label 'ad-hoc' -AdHoc -VirtualPrefix $prefix
  $names = @($entries | ForEach-Object { $_.Path })
  if ($null -ne $script:Denylist) {
    $c = Invoke-LiteralContentScan -RepoTop $top -RelDir $rel -Label 'ad-hoc'
    $n = Invoke-LiteralNameScan -Names $names -Label 'ad-hoc'
    if (($c + $n) -eq 0) { Write-Gate 'ad-hoc: literal layer clean' }
  } else { Write-Gate 'ad-hoc: literal layer skipped (denylist not loaded)' }
  if ($null -ne $exe) {
    $before = $script:Failures.Count
    Invoke-PatternScan -GitleaksExe $exe -RepoTop $top -ConfigA $cfg.A -ConfigB $cfg.B -Label 'ad-hoc' -Directory $target
    Invoke-PatternTextScan -GitleaksExe $exe -ConfigA $cfg.A -ConfigB $cfg.B -Label 'ad-hoc' -Texts $names -What 'file names'
    if ($script:Failures.Count -eq $before) { Write-Gate 'ad-hoc: pattern layer clean' }
  } else { Write-Gate 'ad-hoc: pattern layer skipped (scanner not verified)' }
}

# ------------------------------------------------------------------------------------------------
# Entry
# ------------------------------------------------------------------------------------------------

function Invoke-Main {
  if ($ShowPins) {
    [pscustomobject]@{ version = $script:GitleaksVersion; pins = $script:GitleaksPins; allowlistEntries = $script:PinnedAllowlistEntries } | ConvertTo-Json -Compress | Write-Output
    $script:ExitCode = 0; return
  }
  if ($VerifyGitleaks) {
    if (Test-GitleaksPinned $VerifyGitleaks) { Write-Gate "gitleaks $($script:GitleaksVersion) verified by hash"; $script:ExitCode = 0; return }
    Write-Gate 'gitleaks hash mismatch'; $script:ExitCode = 1; return
  }
  $modes = @(); if ($Hook) { $modes += 'hook' }; if ($Ci) { $modes += 'ci' }; if ($Path) { $modes += 'path' }
  if ($modes.Count -ne 1) { Write-Gate 'usage: -Hook pre-commit | -Hook pre-push -Remote <name> -RemoteUrl <url> | -Ci | -Path <dir>'; $script:ExitCode = 1; return }
  try {
    switch ($modes[0]) {
      'hook' { if ($Hook -eq 'pre-commit') { Invoke-PreCommit } else { Invoke-PrePush } }
      'ci' { Invoke-CiMode }
      'path' { Invoke-AdHoc }
    }
  } catch {
    Add-Failure "error: $($_.Exception.GetType().Name): $(Format-Safe $_.Exception.Message)"
  }
  if ($script:Failures.Count -gt 0) { Write-Host "gate: FAIL ($($script:Failures.Count) finding(s))"; $script:ExitCode = 1; return }
  Write-Host 'gate: PASS'
  $script:ExitCode = 0
}

Invoke-Main
exit $script:ExitCode
