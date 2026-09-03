#Requires -Version 7
<#
.SYNOPSIS
Fixture suite for the publish-safety gate: throwaway repositories, a synthetic identifier list,
one predicted refusal per rule and one pass per rule.

.DESCRIPTION
Every case builds its scenario in a scratch clone whose hooks were installed by
scripts/install-gate.ps1 with a SYNTHETIC list, then commits or pushes through the real hooks
(or runs the CI and ad-hoc placements directly) and asserts the outcome and the output. Every
refusal also asserts that no line of the synthetic list appears in the output.

Public patterns are never spelled here; samples are built from pieces at run time.

.PARAMETER Gitleaks
Path of the pinned gitleaks binary (default: AOR_GITLEAKS, else ~/.aor/bin/gitleaks/...).

.PARAMETER SourceRoot
The repository root whose scripts/ and .gitleaks.toml are under test (default: this repository).

.PARAMETER Only
Run only the cases whose name matches this wildcard.

.PARAMETER KeepFixtures
Leave the scratch directory in place for inspection.
#>
[CmdletBinding()]
param(
  [string]$Gitleaks = $(if ($env:AOR_GITLEAKS) { $env:AOR_GITLEAKS } else { Join-Path $HOME '.aor' 'bin' 'gitleaks' $(if ($IsWindows) { 'gitleaks.exe' } else { 'gitleaks' }) }),
  [string]$SourceRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
  [string]$Only = '*',
  [switch]$KeepFixtures
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }

# ------------------------------------------------------------------------------------------------
# Harness
# ------------------------------------------------------------------------------------------------

$script:Results = [System.Collections.Generic.List[object]]::new()
$script:Current = $null
$script:CurrentFailures = $null

$tmpBase = if ($IsWindows -and $env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Temp' } else { [IO.Path]::GetTempPath() }
$script:Root = Join-Path $tmpBase ('aor-gt-' + [guid]::NewGuid().ToString('n').Substring(0, 6))
New-Item -ItemType Directory -Path $script:Root | Out-Null
$script:FixtureCount = 0

# The synthetic list: never real identifiers.
$script:List = @('zq-synthetic-employer', 'ZQ-Synth-Prefix', 'zq-user-9', 'zq-instance-a')
$script:ListDir = Join-Path $script:Root 'lists'
New-Item -ItemType Directory -Path $script:ListDir | Out-Null
$script:ListFile = Join-Path $script:ListDir 'denylist.txt'
[IO.File]::WriteAllText($script:ListFile, ("# synthetic list`n" + ($script:List -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))

function Fail([string]$Message) { $script:CurrentFailures.Add($Message) }
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { Fail $Message } }
function Assert-Contains([string]$Text, [string]$Needle, [string]$What = 'output') { if ($Text.IndexOf($Needle, [StringComparison]::OrdinalIgnoreCase) -lt 0) { Fail "$What lacks '$Needle'" } }
function Assert-NotContains([string]$Text, [string]$Needle, [string]$What = 'output') { if ($Text.IndexOf($Needle, [StringComparison]::OrdinalIgnoreCase) -ge 0) { Fail "$What contains '$Needle'" } }
function Assert-NoListedLiteral([string]$Text) { foreach ($l in $script:List) { Assert-NotContains $Text $l 'output (literal leak)' } }

function Invoke-Case([string]$Name, [scriptblock]$Body) {
  if ($Name -notlike $Only) { return }
  $script:Current = $Name
  $script:CurrentFailures = [System.Collections.Generic.List[string]]::new()
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try { & $Body } catch { $script:CurrentFailures.Add("exception $($_.Exception.GetType().Name): $($_.Exception.Message)") }
  $sw.Stop()
  $passed = ($script:CurrentFailures.Count -eq 0)
  $script:Results.Add([pscustomobject]@{ Name = $Name; Passed = $passed; Detail = ($script:CurrentFailures -join ' | '); Ms = $sw.ElapsedMilliseconds })
  Write-Host ("{0} {1} ({2} ms){3}" -f $(if ($passed) { 'PASS' } else { 'FAIL' }), $Name, $sw.ElapsedMilliseconds, $(if ($passed) { '' } else { ': ' + ($script:CurrentFailures -join ' | ') }))
}

function Invoke-Proc([string]$Exe, [string[]]$Arguments, [string]$WorkingDirectory = $null, [hashtable]$Environment = $null, [string]$StdinText = $null) {
  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $Exe
  foreach ($a in $Arguments) { $psi.ArgumentList.Add($a) }
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $utf8 = [Text.UTF8Encoding]::new($false)
  $psi.StandardInputEncoding = $utf8; $psi.StandardOutputEncoding = $utf8; $psi.StandardErrorEncoding = $utf8
  if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
  if ($null -ne $Environment) { foreach ($k in $Environment.Keys) { if ($null -eq $Environment[$k]) { [void]$psi.Environment.Remove($k) } else { $psi.Environment[$k] = [string]$Environment[$k] } } }
  $p = [System.Diagnostics.Process]::Start($psi)
  $err = $p.StandardError.ReadToEndAsync(); $out = $p.StandardOutput.ReadToEndAsync()
  if ($null -ne $StdinText) { $p.StandardInput.Write($StdinText) }
  $p.StandardInput.Close()
  $p.WaitForExit()
  return [pscustomobject]@{ ExitCode = $p.ExitCode; Output = ($out.Result + "`n" + $err.Result) }
}

function Git($Fixture, [string[]]$Arguments, [hashtable]$Environment = $null) { return Invoke-Proc 'git' $Arguments $Fixture.Work $Environment }
function Gate($Fixture, [string[]]$Arguments, [hashtable]$Environment = $null) { return Invoke-Proc 'pwsh' (@('-NoProfile', '-NonInteractive', '-File', (Join-Path $Fixture.Work 'scripts' 'publish-gate.ps1')) + $Arguments) $Fixture.Work $Environment }

function Write-File($Fixture, [string]$RelPath, [string]$Text) {
  $full = Join-Path $Fixture.Work ($RelPath -replace '/', [IO.Path]::DirectorySeparatorChar)
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $full) | Out-Null
  [IO.File]::WriteAllText($full, $Text, [Text.UTF8Encoding]::new($false))
}
function Write-Bytes($Fixture, [string]$RelPath, [byte[]]$Bytes) {
  $full = Join-Path $Fixture.Work ($RelPath -replace '/', [IO.Path]::DirectorySeparatorChar)
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $full) | Out-Null
  [IO.File]::WriteAllBytes($full, $Bytes)
}

function Get-PatternSamples {
  $bs = [string][char]92
  return [ordered]@{
    'aor-windows-user-path'      = 'C:' + $bs + 'Us' + 'ers' + $bs + 'jd'
    'aor-posix-user-path'        = ' /' + 'ho' + 'me' + '/jd'
    'aor-synced-folder-path'          = 'One' + 'Drive'
    'aor-claude-project-slug'    = 'C--' + 'Us' + 'ers-' + 'x'
    'aor-unresolved-placeholder' = '<' + 'AOR' + '_X>'
  }
}

# Commit everything in the work tree through the hooks; returns the process result.
function Commit($Fixture, [string]$Message = 'change', [switch]$NoVerify, [hashtable]$Environment = $null) {
  [void](Git $Fixture @('add', '-A'))
  $a = @('commit', '-q', '-m', $Message)
  if ($NoVerify) { $a += '--no-verify' }
  return Git $Fixture $a $Environment
}
function Push($Fixture, [string[]]$Refspecs = @('main'), [hashtable]$Environment = $null, [switch]$Force) {
  $a = @('push', '-q')
  if ($Force) { $a += '--force' }
  $a += 'origin'; $a += $Refspecs
  return Git $Fixture $a $Environment
}
function Reset-ToRemote($Fixture) {
  [void](Git $Fixture @('reset', '-q', '--hard', 'origin/main'))
  [void](Git $Fixture @('clean', '-fdq'))
}
function Reset-Work($Fixture) {
  [void](Git $Fixture @('reset', '-q', '--hard'))
  [void](Git $Fixture @('clean', '-fdq'))
}

function Copy-GateFiles([string]$Work) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Work 'scripts') | Out-Null
  foreach ($f in 'publish-gate.ps1', 'install-gate.ps1', 'get-gitleaks.ps1') { Copy-Item -LiteralPath (Join-Path $SourceRoot 'scripts' $f) -Destination (Join-Path $Work 'scripts' $f) }
  Copy-Item -LiteralPath (Join-Path $SourceRoot '.gitleaks.toml') -Destination (Join-Path $Work '.gitleaks.toml')
  [IO.File]::WriteAllText((Join-Path $Work 'README.md'), "# fixture`n", [Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText((Join-Path $Work 'LICENSE'), "MIT`n", [Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText((Join-Path $Work '.gitignore'), ".venv/`n", [Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText((Join-Path $Work '.gitattributes'), "* text=auto`n", [Text.UTF8Encoding]::new($false))
}

# A fixture: a clone with the gate installed (synthetic list), a bare remote, a pushed baseline.
function New-Fixture([switch]$SkipInstall, [string]$OriginUrl = $null, [string]$ListFile = $null) {
  $script:FixtureCount++
  $dir = Join-Path $script:Root ("f{0:d2}" -f $script:FixtureCount)
  $work = Join-Path $dir 'w'; $bare = Join-Path $dir 'r.git'
  New-Item -ItemType Directory -Path $work | Out-Null
  $r = Invoke-Proc 'git' @('init', '-q', '--bare', '-b', 'main', $bare); if ($r.ExitCode -ne 0) { throw 'git init --bare failed' }
  $r = Invoke-Proc 'git' @('init', '-q', '-b', 'main', $work); if ($r.ExitCode -ne 0) { throw 'git init failed' }
  $fx = [pscustomobject]@{ Work = $work; Bare = $bare; Dir = $dir }
  [void](Git $fx @('config', 'user.name', 'fixture'))
  [void](Git $fx @('config', 'user.email', 'fixture@example.invalid'))
  [void](Git $fx @('config', 'commit.gpgsign', 'false'))
  [void](Git $fx @('remote', 'add', 'origin', $(if ($OriginUrl) { $OriginUrl } else { $bare })))
  Copy-GateFiles $work
  if (-not $SkipInstall) {
    $list = if ($ListFile) { $ListFile } else { $script:ListFile }
    $i = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $work 'scripts' 'install-gate.ps1'), '-GitHubUser', 'fixture', '-Denylist', $list, '-Gitleaks', $Gitleaks) $work
    if ($i.ExitCode -ne 0) { throw "installer failed in fixture: $($i.Output)" }
    $c = Commit $fx 'baseline'
    if ($c.ExitCode -ne 0) { throw "baseline commit refused: $($c.Output)" }
    if (-not $OriginUrl) {
      $p = Push $fx
      if ($p.ExitCode -ne 0) { throw "baseline push refused: $($p.Output)" }
    }
  }
  return $fx
}

function Add-Plugin($Fixture, [string]$Name = 'aor-t', [string]$Version = '0.1.0', [string]$SkillName = 'aor-t-skill', [string]$Body = 'body') {
  Write-File $Fixture "plugins/$Name/.claude-plugin/plugin.json" ("{`"name`":`"$Name`",`"version`":`"$Version`",`"description`":`"fixture plugin`",`"author`":{`"name`":`"fixture`"},`"license`":`"MIT`"}`n")
  Write-File $Fixture "plugins/$Name/skills/$SkillName/SKILL.md" "---`nname: $SkillName`ndescription: fixture skill`n---`n$Body`n"
  Write-File $Fixture '.claude-plugin/marketplace.json' ("{`"name`":`"aor`",`"owner`":{`"name`":`"fixture`"},`"plugins`":[{`"name`":`"$Name`",`"source`":`"./plugins/$Name`",`"description`":`"fixture plugin`"}]}`n")
}

function Set-GateConstant($Fixture, [string]$Pattern, [string]$Replacement) {
  $file = Join-Path $Fixture.Work 'scripts' 'publish-gate.ps1'
  $text = [IO.File]::ReadAllText($file, [Text.UTF8Encoding]::new($false))
  if (-not $text.Contains($Pattern)) { if ($text.Contains($Replacement)) { return }; throw "gate constant not found: $Pattern" }
  [IO.File]::WriteAllText($file, $text.Replace($Pattern, $Replacement), [Text.UTF8Encoding]::new($false))
}

# ------------------------------------------------------------------------------------------------
# Pre-commit cases (fixture A; every case leaves the tree as it found it)
# ------------------------------------------------------------------------------------------------

$A = New-Fixture
$samples = Get-PatternSamples

function Expect-CommitRefused($Fixture, [string[]]$Needles, [string]$Message = 'change') {
  $r = Commit $Fixture $Message
  Assert-True ($r.ExitCode -ne 0) 'commit should be refused'
  Assert-Contains $r.Output 'gate: FAIL'
  foreach ($n in $Needles) { Assert-Contains $r.Output $n }
  Assert-NoListedLiteral $r.Output
  Reset-Work $Fixture
  return $r
}
function Expect-CommitAccepted($Fixture, [string[]]$Needles = @(), [string]$Message = 'change') {
  $r = Commit $Fixture $Message
  Assert-True ($r.ExitCode -eq 0) "commit should pass: $($r.Output)"
  Assert-Contains $r.Output 'gate: PASS'
  foreach ($n in $Needles) { Assert-Contains $r.Output $n }
  return $r
}

Invoke-Case 'pc-literal-content' {
  Write-File $A 'scripts/note.md' "see $($script:List[2]) here`n"
  $r = Expect-CommitRefused $A @('listed identifier in content', 'scripts/note.md', 'lines 1', '1 match line(s)')
}
Invoke-Case 'pc-literal-content-case-insensitive' {
  Write-File $A 'scripts/note.md' "see $($script:List[1].ToUpperInvariant()) here`n"
  [void](Expect-CommitRefused $A @('listed identifier in content', 'scripts/note.md'))
}
Invoke-Case 'pc-literal-filename' {
  Write-File $A "scripts/$($script:List[0])-note.md" "clean`n"
  [void](Expect-CommitRefused $A @('listed identifier in 1 file name(s)', 'names are withheld'))
}
# Plain script blocks: Invoke-Case runs each one synchronously, so $id is the current key. A closure
# (GetNewClosure) would not see this script's functions when the suite is invoked with call semantics,
# which is how GitHub's pwsh shell runs a step.
foreach ($id in $samples.Keys) {
  Invoke-Case "pc-pattern-$id" {
    Write-File $A 'scripts/p.md' "text $($samples[$id]) end`n"
    [void](Expect-CommitRefused $A @("pattern $id (default+own)", "pattern $id (own-only)", 'scripts/p.md'))
  }
}
Invoke-Case 'pc-pattern-placeholder-name-does-not-match' {
  Write-File $A 'scripts/p.md' ("stored under /" + 'ho' + 'me/<user>/x and https://api.github.com/users/octocat' + "`n")
  [void](Expect-CommitAccepted $A)
  Reset-ToRemote $A
}
Invoke-Case 'pc-toplevel-set' {
  Write-File $A 'notes.md' "x`n"
  [void](Expect-CommitRefused $A @('top-level entry not permitted: file:notes.md'))
  Write-File $A 'docs/x.md' "x`n"
  [void](Expect-CommitRefused $A @('top-level entry not permitted: dir:docs'))
}
Invoke-Case 'pc-noise-name' {
  foreach ($n in 'session-log-1.md', 'my-handover.md', 'x-payload.json', 'readout.html', 'panel-notes.md', 'q-record.md', 'a.log', 'b.jsonl', 'c.md.bak') {
    Write-File $A "scripts/$n" "x`n"
    [void](Expect-CommitRefused $A @('noise-shaped file name', "scripts/$n"))
  }
}
Invoke-Case 'pc-noise-name-directory-allowed' {
  Write-File $A 'legacy/aor-decision-readout/SKILL.md' "---`nname: aor-decision-readout`ndescription: x`n---`nx`n"
  [void](Expect-CommitAccepted $A)
  Reset-ToRemote $A
}
Invoke-Case 'pc-noise-directory' {
  Write-File $A 'scripts/captures/x.md' "x`n"
  [void](Expect-CommitRefused $A @('noise directory name (captures)'))
  Write-File $A 'scripts/__pycache__/x.md' "x`n"
  [void](Expect-CommitRefused $A @('noise directory name (__pycache__)'))
}
Invoke-Case 'pc-skill-placement' {
  Write-File $A 'scripts/SKILL.md' "---`nname: scripts`n---`nx`n"
  [void](Expect-CommitRefused $A @('SKILL.md outside plugins/aor-*/skills/<name>/ or legacy/<name>/'))
  Write-File $A 'legacy/deep/sub/SKILL.md' "---`nname: sub`n---`nx`n"
  [void](Expect-CommitRefused $A @('SKILL.md outside'))
}
Invoke-Case 'pc-skill-name-mismatch' {
  Write-File $A 'legacy/foo/SKILL.md' "---`nname: bar`ndescription: x`n---`nx`n"
  [void](Expect-CommitRefused $A @('frontmatter name does not equal its directory name', 'legacy/foo/SKILL.md'))
  Write-File $A 'legacy/foo/SKILL.md' "no frontmatter`n"
  [void](Expect-CommitRefused $A @('SKILL.md has no frontmatter name'))
}
Invoke-Case 'pc-skill-duplicate' {
  Add-Plugin $A -SkillName 'dup'
  Write-File $A 'legacy/dup/SKILL.md' "---`nname: dup`ndescription: x`n---`nx`n"
  [void](Expect-CommitRefused $A @('duplicate skill name across plugins and legacy: dup'))
}
Invoke-Case 'pc-manifest-missing' {
  Write-File $A 'plugins/aor-x/skills/s/SKILL.md' "---`nname: s`ndescription: x`n---`nx`n"
  Write-File $A '.claude-plugin/marketplace.json' "{`"name`":`"aor`",`"plugins`":[{`"name`":`"aor-x`",`"source`":`"./plugins/aor-x`"}]}`n"
  [void](Expect-CommitRefused $A @('missing plugins/aor-x/.claude-plugin/plugin.json'))
}
Invoke-Case 'pc-manifest-fields' {
  Add-Plugin $A
  Write-File $A 'plugins/aor-t/.claude-plugin/plugin.json' "{`"name`":`"aor-other`",`"version`":`"1`",`"author`":`"`"}`n"
  [void](Expect-CommitRefused $A @('name must equal the directory name', 'version must be a semver string', 'must carry an author', 'must carry a license'))
}
Invoke-Case 'pc-plugin-dir-name' {
  Add-Plugin $A -Name 'other'
  [void](Expect-CommitRefused $A @('plugin directory name must be aor-<family>'))
}
Invoke-Case 'pc-marketplace-missing' {
  Add-Plugin $A
  Remove-Item -Force -LiteralPath (Join-Path $A.Work '.claude-plugin' 'marketplace.json')
  [void](Expect-CommitRefused $A @('marketplace.json is required when plugins/ exists'))
}
Invoke-Case 'pc-marketplace-count' {
  Add-Plugin $A
  Write-File $A '.claude-plugin/marketplace.json' "{`"name`":`"aor`",`"plugins`":[{`"name`":`"aor-t`",`"source`":`"./plugins/aor-t`"},{`"name`":`"aor-t2`",`"source`":`"plugins/aor-t/`"}]}`n"
  [void](Expect-CommitRefused $A @('marketplace must list plugins/aor-t exactly once (found 2)'))
  Write-File $A '.claude-plugin/marketplace.json' "{`"name`":`"aor`",`"plugins`":[{`"name`":`"aor-t`",`"source`":`"./plugins/aor-t`"},{`"name`":`"aor-ghost`",`"source`":`"./plugins/aor-ghost`"}]}`n"
  [void](Expect-CommitRefused $A @('marketplace entry points at a plugin directory that does not exist'))
}
Invoke-Case 'pc-clean-plugin' {
  Add-Plugin $A
  [void](Expect-CommitAccepted $A)
  Reset-ToRemote $A
}
Invoke-Case 'pc-size-cap' {
  Write-Bytes $A 'scripts/big.txt' ([Text.Encoding]::ASCII.GetBytes('a' * 1048577))
  [void](Expect-CommitRefused $A @('file over 1048576 bytes'))
}
Invoke-Case 'pc-size-cap-boundary-passes' {
  $bytes = [byte[]]::new(1048576); for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = 97 }
  Write-Bytes $A 'scripts/big.txt' $bytes
  [void](Expect-CommitAccepted $A)
  Reset-ToRemote $A
}
Invoke-Case 'pc-binary' {
  Write-Bytes $A 'scripts/x.pyc' ([byte[]](0..255))
  [void](Expect-CommitRefused $A @('binary file: scripts/x.pyc'))
  Write-Bytes $A 'scripts/img.png' ([byte[]](137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13))
  [void](Expect-CommitRefused $A @('binary file: scripts/img.png'))
}
Invoke-Case 'pc-inherited-allowlist-svg-literal' {
  Write-File $A 'scripts/logo.svg' "<svg><text>$($script:List[3])</text></svg>`n"
  [void](Expect-CommitRefused $A @('listed identifier in content', 'scripts/logo.svg'))
}
Invoke-Case 'pc-inherited-allowlist-svg-pattern' {
  Write-File $A 'scripts/logo.svg' "<svg><text>$($samples['aor-synced-folder-path'])</text></svg>`n"
  [void](Expect-CommitRefused $A @('pattern aor-synced-folder-path (own-only)', 'scripts/logo.svg'))
}
Invoke-Case 'pc-inherited-allowlist-lockfile-pattern' {
  Write-File $A 'scripts/package-lock.json' "{`"path`":`"$($samples['aor-claude-project-slug'])`"}`n"
  [void](Expect-CommitRefused $A @('pattern aor-claude-project-slug (own-only)', 'scripts/package-lock.json'))
}
Invoke-Case 'pc-inherited-allowlist-node-modules-literal' {
  Write-File $A 'scripts/node_modules/x.js' "// $($script:List[0])`n"
  [void](Expect-CommitRefused $A @('listed identifier in content', 'scripts/node_modules/x.js'))
}
Invoke-Case 'pc-inherited-allowlist-config-name-pattern' {
  Write-File $A 'scripts/gitleaks.toml' "# $($samples['aor-unresolved-placeholder'])`n"
  [void](Expect-CommitRefused $A @('pattern aor-unresolved-placeholder (own-only)', 'scripts/gitleaks.toml'))
}
Invoke-Case 'pc-gitleaksignore' {
  Write-File $A '.gitleaksignore' "abc:1`n"
  [void](Expect-CommitRefused $A @('top-level entry not permitted: file:.gitleaksignore', '.gitleaksignore is not permitted anywhere'))
  Write-File $A 'scripts/.gitleaksignore' "abc:1`n"
  [void](Expect-CommitRefused $A @('.gitleaksignore is not permitted anywhere'))
}
Invoke-Case 'pc-inline-allow-comment-ignored' {
  Write-File $A 'scripts/p.md' "x $($samples['aor-synced-folder-path']) gitleaks:allow`n"
  [void](Expect-CommitRefused $A @('pattern aor-synced-folder-path'))
}
Invoke-Case 'pc-allowlist-count' {
  $toml = [IO.File]::ReadAllText((Join-Path $A.Work '.gitleaks.toml'))
  Write-File $A '.gitleaks.toml' ($toml + "`n[[allowlists]]`ndescription = `"fixture exception for a test`"`npaths = ['''scripts/x\.md''']`n")
  [void](Expect-CommitRefused $A @('holds 1 allowlist entries; the gate pins 0'))
}
Invoke-Case 'pc-allowlist-shape' {
  # every refused commit resets the work tree, which restores the tracked gate script, so the pin is patched before each step
  $pin1 = { Set-GateConstant $A '$script:PinnedAllowlistEntries = 0' '$script:PinnedAllowlistEntries = 1' }
  try {
    $toml = [IO.File]::ReadAllText((Join-Path $A.Work '.gitleaks.toml'))
    & $pin1; Write-File $A '.gitleaks.toml' ($toml + "`n[[allowlists]]`npaths = ['''scripts/x\.md''']`n")
    [void](Expect-CommitRefused $A @('must start with a description'))
    & $pin1; Write-File $A '.gitleaks.toml' ($toml + "`n[[allowlists]]`ndescription = `"fixture exception for a test`"`n")
    [void](Expect-CommitRefused $A @('is not scoped'))
    & $pin1; Write-File $A '.gitleaks.toml' ($toml + "`n[allowlist]`ndescription = `"legacy spelling`"`npaths = ['''x''']`n")
    [void](Expect-CommitRefused $A @('must be spelled [[allowlists]]'))
    & $pin1; Write-File $A '.gitleaks.toml' ($toml + "`n[[allowlists]]`ndescription = `"fixture exception for a test`"`npaths = ['''scripts/x\.md''']`n")
    [void](Expect-CommitAccepted $A)
  } finally { Reset-ToRemote $A; Set-GateConstant $A '$script:PinnedAllowlistEntries = 1' '$script:PinnedAllowlistEntries = 0' }
}
Invoke-Case 'pc-config-missing-rule' {
  $toml = [IO.File]::ReadAllText((Join-Path $A.Work '.gitleaks.toml'))
  Write-File $A '.gitleaks.toml' ($toml -replace 'id = "aor-synced-folder-path"', 'id = "aor-renamed"')
  [void](Expect-CommitRefused $A @('lacks rule(s) the gate expects: aor-synced-folder-path', 'no control sample for: aor-renamed', 'positive control failed'))
}
Invoke-Case 'pc-config-removed' {
  Remove-Item -Force -LiteralPath (Join-Path $A.Work '.gitleaks.toml')
  [void](Expect-CommitRefused $A @('.gitleaks.toml is missing from the staged tree'))
}
Invoke-Case 'pc-hash-mismatch' {
  [void](Git $A @('config', '--local', 'aor.gitleaks', (Join-Path $A.Work 'LICENSE')))
  try {
    Write-File $A 'scripts/z.md' "x`n"
    [void](Expect-CommitRefused $A @('scanner hash mismatch', 'pattern layer skipped', 'literal layer clean'))
  } finally { [void](Git $A @('config', '--local', 'aor.gitleaks', $Gitleaks)) }
}
Invoke-Case 'pc-denylist-missing-key' {
  [void](Git $A @('config', '--local', '--unset', 'aor.denylist'))
  try {
    Write-File $A 'scripts/z.md' "x`n"
    [void](Expect-CommitRefused $A @('denylist not loaded (aor.denylist is not set', 'literal layer skipped', 'pattern layer clean'))
  } finally { [void](Git $A @('config', '--local', 'aor.denylist', $script:ListFile)) }
}
Invoke-Case 'pc-denylist-missing-file' {
  [void](Git $A @('config', '--local', 'aor.denylist', (Join-Path $script:ListDir 'nope.txt')))
  try { Write-File $A 'scripts/z.md' "x`n"; [void](Expect-CommitRefused $A @('denylist not loaded (the configured file is missing)')) }
  finally { [void](Git $A @('config', '--local', 'aor.denylist', $script:ListFile)) }
}
Invoke-Case 'pc-denylist-empty-and-comment-only' {
  $empty = Join-Path $script:ListDir 'empty.txt'; [IO.File]::WriteAllText($empty, '')
  $comments = Join-Path $script:ListDir 'comments.txt'; [IO.File]::WriteAllText($comments, "# one`n# two`n`n")
  try {
    [void](Git $A @('config', '--local', 'aor.denylist', $empty))
    Write-File $A 'scripts/z.md' "x`n"; [void](Expect-CommitRefused $A @('denylist not loaded (no usable line'))
    [void](Git $A @('config', '--local', 'aor.denylist', $comments))
    Write-File $A 'scripts/z.md' "x`n"; [void](Expect-CommitRefused $A @('denylist not loaded (no usable line'))
  } finally { [void](Git $A @('config', '--local', 'aor.denylist', $script:ListFile)) }
}
Invoke-Case 'pc-denylist-inside-worktree' {
  $inside = Join-Path $A.Work 'list.txt'
  Copy-Item -LiteralPath $script:ListFile -Destination $inside
  [void](Git $A @('config', '--local', 'aor.denylist', $inside))
  try { Write-File $A 'scripts/z.md' "x`n"; [void](Expect-CommitRefused $A @('denylist not loaded (the configured file sits inside a git work tree)')) }
  finally { [void](Git $A @('config', '--local', 'aor.denylist', $script:ListFile)); Reset-Work $A }
}
Invoke-Case 'pc-denylist-crlf' {
  $crlf = Join-Path $script:ListDir 'crlf.txt'
  [IO.File]::WriteAllText($crlf, ("# crlf`r`n" + ($script:List -join "`r`n") + "`r`n"), [Text.UTF8Encoding]::new($false))
  [void](Git $A @('config', '--local', 'aor.denylist', $crlf))
  try {
    Write-File $A 'scripts/z.md' "see $($script:List[2]) here`n"
    [void](Expect-CommitRefused $A @('listed identifier in content', 'scripts/z.md'))
    Write-File $A 'scripts/z.md' "clean`n"
    [void](Expect-CommitAccepted $A @('positive control passed'))
    Reset-ToRemote $A
  } finally { [void](Git $A @('config', '--local', 'aor.denylist', $script:ListFile)) }
}
Invoke-Case 'pc-positive-control-failure' {
  Set-GateConstant $A "`$script:ControlWrapper = 'control {0} control'" "`$script:ControlWrapper = 'control  control'"
  try { Write-File $A 'scripts/z.md' "x`n"; [void](Expect-CommitRefused $A @('positive control failed: the literal layer', 'positive control failed: the name and metadata check')) }
  finally { Set-GateConstant $A "`$script:ControlWrapper = 'control  control'" "`$script:ControlWrapper = 'control {0} control'" }
}
Invoke-Case 'pc-partial-commit-uses-the-temporary-index' {
  Write-File $A 'scripts/clean.md' "clean`n"
  Write-File $A 'scripts/dirty.md' "see $($script:List[2])`n"
  [void](Git $A @('add', '-A'))
  $r = Git $A @('commit', '-q', '-m', 'partial', '--', 'scripts/clean.md')
  Assert-True ($r.ExitCode -eq 0) "a partial commit of the clean path only must pass (the hook sees the temporary index): $($r.Output)"
  Assert-Contains $r.Output 'gate: PASS'
  $r2 = Git $A @('commit', '-q', '-m', 'the rest')
  Assert-True ($r2.ExitCode -ne 0) 'the remaining staged file carries a literal and must be refused'
  Assert-Contains $r2.Output 'listed identifier in content'
  Assert-Contains $r2.Output 'scripts/dirty.md'
  Assert-NoListedLiteral $r2.Output
  Reset-ToRemote $A
}

# ------------------------------------------------------------------------------------------------
# Pre-push cases (fixture B, a progression)
# ------------------------------------------------------------------------------------------------

$B = New-Fixture

function Expect-PushRefused($Fixture, [string[]]$Refspecs, [string[]]$Needles, [hashtable]$Environment = $null, [switch]$Force) {
  $r = Push $Fixture $Refspecs $Environment -Force:$Force
  Assert-True ($r.ExitCode -ne 0) 'push should be refused'
  Assert-Contains $r.Output 'gate: FAIL'
  foreach ($n in $Needles) { Assert-Contains $r.Output $n }
  Assert-NoListedLiteral $r.Output
  return $r
}
function Expect-PushAccepted($Fixture, [string[]]$Refspecs, [string[]]$Needles = @(), [hashtable]$Environment = $null, [switch]$Force) {
  $r = Push $Fixture $Refspecs $Environment -Force:$Force
  Assert-True ($r.ExitCode -eq 0) "push should pass: $($r.Output)"
  Assert-Contains $r.Output 'gate: PASS'
  foreach ($n in $Needles) { Assert-Contains $r.Output $n }
  return $r
}

Invoke-Case 'pp-literal-commit-message' {
  Write-File $B 'scripts/a.md' "clean`n"
  $c = Commit $B "mention $($script:List[2]) here"
  Assert-True ($c.ExitCode -eq 0) 'pre-commit does not see the message'
  [void](Expect-PushRefused $B @('main') @('listed identifier in the author, committer or message of commit'))
  Reset-ToRemote $B
}
Invoke-Case 'pp-literal-author' {
  Write-File $B 'scripts/a.md' "clean`n"
  [void](Git $B @('add', '-A'))
  $c = Git $B @('-c', "user.name=$($script:List[0])", 'commit', '-q', '-m', 'clean message')
  Assert-True ($c.ExitCode -eq 0) 'pre-commit does not see the author'
  [void](Expect-PushRefused $B @('main') @('listed identifier in the author, committer or message of commit'))
  Reset-ToRemote $B
}
Invoke-Case 'pp-literal-in-history-tip-clean' {
  Write-File $B 'scripts/a.md' "see $($script:List[1])`n"
  $c = Commit $B 'bad' -NoVerify
  Assert-True ($c.ExitCode -eq 0) '--no-verify commit'
  Remove-Item -Force -LiteralPath (Join-Path $B.Work 'scripts' 'a.md')
  $c2 = Commit $B 'remove again'
  Assert-True ($c2.ExitCode -eq 0) 'tip is clean'
  $r = Expect-PushRefused $B @('main') @('listed identifier in content in ', 'scripts/a.md', '2 commit(s) in range')
  Reset-ToRemote $B
}
Invoke-Case 'pp-pattern-in-history-tip-clean' {
  Write-File $B 'scripts/a.md' "x $($samples['aor-windows-user-path']) y`n"
  [void](Commit $B 'bad' -NoVerify)
  Remove-Item -Force -LiteralPath (Join-Path $B.Work 'scripts' 'a.md')
  [void](Commit $B 'remove again')
  [void](Expect-PushRefused $B @('main') @('pattern aor-windows-user-path (default+own) in ', 'scripts/a.md'))
  Reset-ToRemote $B
}
Invoke-Case 'pp-tip-tree-rules' {
  Write-File $B 'notes.md' "x`n"
  [void](Commit $B 'noise' -NoVerify)
  [void](Expect-PushRefused $B @('main') @('top-level entry not permitted: file:notes.md'))
  Reset-ToRemote $B
}
Invoke-Case 'pp-new-plugin-passes-with-validators' {
  Add-Plugin $B
  [void](Expect-CommitAccepted $B)
  [void](Expect-PushAccepted $B @('main') @('plugins/aor-t is new at 0.1.0', 'claude plugin validate passed', 'gh skill publish --dry-run passed'))
}
Invoke-Case 'pp-version-not-bumped' {
  Write-File $B 'plugins/aor-t/skills/aor-t-skill/SKILL.md' "---`nname: aor-t-skill`ndescription: fixture skill`n---`nchanged`n"
  [void](Expect-CommitAccepted $B)
  [void](Expect-PushRefused $B @('main') @('plugins/aor-t changed but its version did not increase (0.1.0 to 0.1.0)'))
}
Invoke-Case 'pp-version-downgrade' {
  Add-Plugin $B -Version '0.0.9' -Body 'changed'
  [void](Expect-CommitAccepted $B)
  [void](Expect-PushRefused $B @('main') @('version did not increase (0.1.0 to 0.0.9)'))
}
Invoke-Case 'pp-version-bumped' {
  Add-Plugin $B -Version '0.1.1' -Body 'changed'
  [void](Expect-CommitAccepted $B)
  [void](Expect-PushAccepted $B @('main') @('plugins/aor-t 0.1.0 to 0.1.1'))
}
Invoke-Case 'pp-version-prerelease-ordering' {
  Add-Plugin $B -Version '0.2.0-beta.1' -Body 'beta'
  [void](Expect-CommitAccepted $B)
  [void](Expect-PushAccepted $B @('main') @('plugins/aor-t 0.1.1 to 0.2.0-beta.1'))
  Add-Plugin $B -Version '0.2.0' -Body 'release'
  [void](Expect-CommitAccepted $B)
  [void](Expect-PushAccepted $B @('main') @('plugins/aor-t 0.2.0-beta.1 to 0.2.0'))
}
Invoke-Case 'pp-tag-ok' {
  [void](Git $B @('tag', 'aor-t--v0.2.0'))
  [void](Expect-PushAccepted $B @('refs/tags/aor-t--v0.2.0'))
}
Invoke-Case 'pp-tag-annotated-ok' {
  [void](Git $B @('tag', '-a', '-m', 'annotated', 'aor-t--v0.2.0-anno'))
  # the name carries a prerelease suffix that does not match the manifest, so it must fail on version, not on shape
  [void](Expect-PushRefused $B @('refs/tags/aor-t--v0.2.0-anno') @('does not match the plugin version at the tagged commit (0.2.0)'))
  [void](Git $B @('tag', '-d', 'aor-t--v0.2.0-anno'))
}
Invoke-Case 'pp-tag-version-mismatch' {
  [void](Git $B @('tag', 'aor-t--v9.9.9'))
  [void](Expect-PushRefused $B @('refs/tags/aor-t--v9.9.9') @('does not match the plugin version at the tagged commit (0.2.0)'))
  [void](Git $B @('tag', '-d', 'aor-t--v9.9.9'))
}
Invoke-Case 'pp-tag-name-shape' {
  [void](Git $B @('tag', 'release-1'))
  [void](Expect-PushRefused $B @('refs/tags/release-1') @('tag name must be <plugin>--v<version>'))
  [void](Git $B @('tag', '-d', 'release-1'))
}
Invoke-Case 'pp-tag-unknown-plugin' {
  [void](Git $B @('tag', 'aor-zz--v0.2.0'))
  [void](Expect-PushRefused $B @('refs/tags/aor-zz--v0.2.0') @('names a plugin that has no manifest at the tagged commit'))
  [void](Git $B @('tag', '-d', 'aor-zz--v0.2.0'))
}
Invoke-Case 'pp-tag-unreachable-from-main' {
  [void](Git $B @('checkout', '-q', '-b', 'side'))
  Add-Plugin $B -Version '0.3.0' -Body 'side'
  [void](Expect-CommitAccepted $B)
  [void](Git $B @('tag', 'aor-t--v0.3.0'))
  [void](Expect-PushRefused $B @('refs/tags/aor-t--v0.3.0') @('points at a commit not reachable from main'))
  [void](Git $B @('tag', '-d', 'aor-t--v0.3.0'))
  [void](Git $B @('checkout', '-q', 'main'))
}
Invoke-Case 'pp-new-branch' {
  [void](Expect-PushAccepted $B @('side') @('new on the remote', '1 commit(s) in range'))
}
Invoke-Case 'pp-branch-delete' {
  [void](Expect-PushAccepted $B @(':refs/heads/side') @('branch deletion; nothing to scan'))
  [void](Git $B @('branch', '-q', '-D', 'side'))
}
Invoke-Case 'pp-main-delete' {
  [void](Expect-PushRefused $B @(':refs/heads/main') @('deleting main is refused'))
}
Invoke-Case 'pp-tag-delete' {
  [void](Expect-PushRefused $B @(':refs/tags/aor-t--v0.2.0') @('tag deletion is refused outside INCIDENT MODE'))
  [void](Expect-PushAccepted $B @(':refs/tags/aor-t--v0.2.0') @('INCIDENT MODE', 'tag deletion permitted in INCIDENT MODE') @{ AOR_GATE_INCIDENT = '1' })
  [void](Git $B @('tag', '-d', 'aor-t--v0.2.0'))
}
Invoke-Case 'pp-non-fast-forward' {
  [void](Git $B @('reset', '-q', '--hard', 'HEAD~1'))
  Write-File $B 'scripts/rewrite.md' "rewritten`n"
  [void](Expect-CommitAccepted $B)
  [void](Expect-PushRefused $B @('main') @('non-fast-forward push to main is refused outside INCIDENT MODE') -Force)
  # INCIDENT MODE still scans: put a literal into the rewrite
  Write-File $B 'scripts/rewrite.md' "rewritten $($script:List[3])`n"
  [void](Commit $B 'bad rewrite' -NoVerify)
  [void](Expect-PushRefused $B @('main') @('INCIDENT MODE', 'non-fast-forward (INCIDENT MODE)', 'listed identifier in content') @{ AOR_GATE_INCIDENT = '1' } -Force)
  [void](Git $B @('reset', '-q', '--hard', 'HEAD~1'))
  [void](Expect-PushAccepted $B @('main') @('INCIDENT MODE', 'non-fast-forward (INCIDENT MODE)') @{ AOR_GATE_INCIDENT = '1' } -Force)
}
Invoke-Case 'pp-remote-tip-unknown' {
  # simulate a stale clone: move the remote ahead through a second clone, then push without fetching
  $other = Join-Path $B.Dir 'other'
  $c = Invoke-Proc 'git' @('clone', '-q', $B.Bare, $other); Assert-True ($c.ExitCode -eq 0) 'clone'
  [void](Invoke-Proc 'git' @('-C', $other, 'config', 'user.name', 'o')); [void](Invoke-Proc 'git' @('-C', $other, 'config', 'user.email', 'o@example.invalid'))
  [IO.File]::WriteAllText((Join-Path $other 'scripts' 'other.md'), "other`n")
  [void](Invoke-Proc 'git' @('-C', $other, 'add', '-A')); [void](Invoke-Proc 'git' @('-C', $other, 'commit', '-q', '-m', 'other'))
  $p = Invoke-Proc 'git' @('-C', $other, 'push', '-q', 'origin', 'main'); Assert-True ($p.ExitCode -eq 0) 'other push (no hooks there)'
  Write-File $B 'scripts/mine.md' "mine`n"
  [void](Expect-CommitAccepted $B)
  [void](Expect-PushRefused $B @('main') @('the remote tip is not known locally; fetch first'))
  [void](Git $B @('fetch', '-q', 'origin')); [void](Git $B @('rebase', '-q', 'origin/main'))
  [void](Expect-PushAccepted $B @('main'))
}

# ------------------------------------------------------------------------------------------------
# CI placement (fixture C; the script run directly with the GitHub environment simulated)
# ------------------------------------------------------------------------------------------------

$C = New-Fixture

function Invoke-CiGate($Fixture, [string]$EventName, [string]$Ref, [hashtable]$Payload, [string]$Denylist, [string]$BaseRef = $null) {
  $eventPath = Join-Path $Fixture.Dir 'event.json'
  [IO.File]::WriteAllText($eventPath, ($Payload | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
  $head = (Git $Fixture @('rev-parse', 'HEAD')).Output.Trim()
  $envMap = @{
    GITHUB_EVENT_NAME = $EventName; GITHUB_REF = $Ref; GITHUB_SHA = $head; GITHUB_EVENT_PATH = $eventPath
    GITHUB_WORKSPACE = $Fixture.Work; GITHUB_BASE_REF = $BaseRef
    AOR_PUBLISH_DENYLIST = $Denylist; AOR_GITLEAKS = $Gitleaks
  }
  return Gate $Fixture @('-Ci') $envMap
}
$ciList = "# ci`n" + ($script:List -join "`n") + "`n"

Invoke-Case 'ci-created-scans-everything' {
  $r = Invoke-CiGate $C 'push' 'refs/heads/main' @{ before = '0000000000000000000000000000000000000000'; created = $true; forced = $false } $ciList
  Assert-True ($r.ExitCode -eq 0) "should pass: $($r.Output)"
  Assert-Contains $r.Output 'range unavailable (ref created)'
  Assert-Contains $r.Output 'denylist loaded'
  Assert-Contains $r.Output 'gate: PASS'
}
Invoke-Case 'ci-range-with-literal-in-middle-commit' {
  $before = (Git $C @('rev-parse', 'HEAD')).Output.Trim()
  Write-File $C 'scripts/a.md' "see $($script:List[0])`n"
  [void](Commit $C 'bad' -NoVerify)
  Remove-Item -Force -LiteralPath (Join-Path $C.Work 'scripts' 'a.md')
  [void](Commit $C 'clean tip')
  $r = Invoke-CiGate $C 'push' 'refs/heads/main' @{ before = $before; created = $false; forced = $false } $ciList
  Assert-True ($r.ExitCode -ne 0) 'should fail'
  Assert-Contains $r.Output '2 commit(s)'
  Assert-Contains $r.Output 'listed identifier in content in '
  Assert-NoListedLiteral $r.Output
  Reset-ToRemote $C
}
Invoke-Case 'ci-empty-secret-fails-but-other-layers-report' {
  $before = (Git $C @('rev-parse', 'HEAD')).Output.Trim()
  Write-File $C 'scripts/clean.md' "clean`n"
  [void](Commit $C 'clean')
  $r = Invoke-CiGate $C 'push' 'refs/heads/main' @{ before = $before; created = $false; forced = $false } ''
  Assert-True ($r.ExitCode -ne 0) 'should fail closed'
  Assert-Contains $r.Output 'denylist not loaded (AOR_PUBLISH_DENYLIST is empty or absent)'
  Assert-Contains $r.Output 'literal layer skipped'
  Assert-Contains $r.Output 'pattern layer clean'
  Assert-NotContains $r.Output 'top-level entry not permitted'
  Reset-ToRemote $C
}
Invoke-Case 'ci-never-prints-the-count' {
  $r = Invoke-CiGate $C 'push' 'refs/heads/main' @{ before = '0000000000000000000000000000000000000000'; created = $true; forced = $false } $ciList
  Assert-NotContains $r.Output "$($script:List.Count) line"
  Assert-NotContains $r.Output "$($script:List.Count) identifier"
}
Invoke-Case 'ci-version-check-on-main' {
  Add-Plugin $C
  [void](Expect-CommitAccepted $C)
  $before = (Git $C @('rev-parse', 'HEAD')).Output.Trim()
  Add-Plugin $C -Body 'changed'
  [void](Expect-CommitAccepted $C)
  $r = Invoke-CiGate $C 'push' 'refs/heads/main' @{ before = $before; created = $false; forced = $false } $ciList
  Assert-True ($r.ExitCode -ne 0) 'should fail on the version'
  Assert-Contains $r.Output 'version did not increase (0.1.0 to 0.1.0)'
  $r2 = Invoke-CiGate $C 'push' 'refs/heads/main' @{ before = $before; created = $false; forced = $true } $ciList
  Assert-True ($r2.ExitCode -eq 0) 'forced: version check skipped'
  Assert-Contains $r2.Output 'range unavailable (ref rewritten (forced))'
}
Invoke-Case 'ci-pull-request-to-main' {
  $base = (Git $C @('rev-parse', 'HEAD~1')).Output.Trim()
  $r = Invoke-CiGate $C 'pull_request' 'refs/pull/1/merge' @{ pull_request = @{ base = @{ sha = $base }; head = @{ sha = 'x' } } } $ciList 'main'
  Assert-True ($r.ExitCode -ne 0) 'PR to main applies the version check'
  Assert-Contains $r.Output 'version did not increase'
  Reset-ToRemote $C
}
Invoke-Case 'ci-tag-push' {
  Add-Plugin $C -Version '0.1.0'
  [void](Expect-CommitAccepted $C)
  [void](Expect-PushAccepted $C @('main'))
  [void](Git $C @('tag', 'aor-t--v0.1.0'))
  $r = Invoke-CiGate $C 'push' 'refs/tags/aor-t--v0.1.0' @{ before = '0000000000000000000000000000000000000000'; created = $true; forced = $false } $ciList
  Assert-True ($r.ExitCode -eq 0) "tag push should pass: $($r.Output)"
  [void](Git $C @('tag', 'aor-t--v0.9.9'))
  $r2 = Invoke-CiGate $C 'push' 'refs/tags/aor-t--v0.9.9' @{ before = '0000000000000000000000000000000000000000'; created = $true; forced = $false } $ciList
  Assert-True ($r2.ExitCode -ne 0) 'mismatching tag fails in CI'
  Assert-Contains $r2.Output 'does not match the plugin version'
}

# ------------------------------------------------------------------------------------------------
# Ad-hoc placement (fixture D)
# ------------------------------------------------------------------------------------------------

$D = New-Fixture

Invoke-Case 'adhoc-pass' {
  Write-File $D 'incubator/aor-u/.claude-plugin/plugin.json' "{`"name`":`"aor-u`",`"version`":`"0.1.0`",`"author`":{`"name`":`"f`"},`"license`":`"MIT`"}`n"
  Write-File $D 'incubator/aor-u/skills/aor-u-skill/SKILL.md' "---`nname: aor-u-skill`ndescription: x`n---`nx`n"
  [void](Git $D @('add', 'incubator'))
  $r = Gate $D @('-Path', (Join-Path $D.Work 'incubator' 'aor-u'))
  Assert-True ($r.ExitCode -eq 0) "should pass: $($r.Output)"
  Assert-Contains $r.Output 'ad-hoc on incubator/aor-u as plugins/aor-u/'
  Assert-Contains $r.Output 'gate: PASS'
}
Invoke-Case 'adhoc-refusals' {
  Write-File $D 'incubator/aor-u/skills/aor-u-skill/notes-readout.md' "see $($script:List[2])`n"
  Write-File $D 'incubator/aor-u/SKILL.md' "---`nname: aor-u`n---`n"
  Write-File $D 'incubator/aor-u/skills/aor-u-skill/p.md' "x $($samples['aor-posix-user-path']) y`n"
  [void](Git $D @('add', 'incubator'))
  $r = Gate $D @('-Path', (Join-Path $D.Work 'incubator' 'aor-u'))
  Assert-True ($r.ExitCode -ne 0) 'should fail'
  Assert-Contains $r.Output 'noise-shaped file name (matches *readout*): plugins/aor-u/skills/aor-u-skill/notes-readout.md'
  Assert-Contains $r.Output 'SKILL.md outside plugins/aor-*/skills/<name>/ or legacy/<name>/: plugins/aor-u/SKILL.md'
  Assert-Contains $r.Output 'listed identifier in content: incubator/aor-u/skills/aor-u-skill/notes-readout.md'
  Assert-Contains $r.Output 'pattern aor-posix-user-path'
  Assert-NoListedLiteral $r.Output
  Reset-Work $D
}
Invoke-Case 'adhoc-fails-closed-without-config' {
  Write-File $D 'incubator/aor-u/skills/aor-u-skill/SKILL.md' "---`nname: aor-u-skill`ndescription: x`n---`nx`n"
  [void](Git $D @('add', 'incubator'))
  [void](Git $D @('config', '--local', '--unset', 'aor.denylist'))
  try {
    $r = Gate $D @('-Path', (Join-Path $D.Work 'incubator' 'aor-u'))
    Assert-True ($r.ExitCode -ne 0) 'should fail closed'
    Assert-Contains $r.Output 'denylist not loaded (aor.denylist is not set'
  } finally { [void](Git $D @('config', '--local', 'aor.denylist', $script:ListFile)); Reset-Work $D }
}
Invoke-Case 'adhoc-path-must-be-inside-a-work-tree' {
  $r = Gate $D @('-Path', $script:ListDir)
  Assert-True ($r.ExitCode -ne 0) 'should fail'
  Assert-Contains $r.Output 'gate: FAIL'
}

# ------------------------------------------------------------------------------------------------
# Installer cases
# ------------------------------------------------------------------------------------------------

Invoke-Case 'install-refuses-nested-clone' {
  $outer = Join-Path $script:Root 'outer'
  New-Item -ItemType Directory -Path $outer | Out-Null
  [void](Invoke-Proc 'git' @('init', '-q', $outer))
  $inner = Join-Path $outer 'inner'
  New-Item -ItemType Directory -Path $inner | Out-Null
  [void](Invoke-Proc 'git' @('init', '-q', '-b', 'main', $inner))
  [void](Invoke-Proc 'git' @('-C', $inner, 'remote', 'add', 'origin', $script:ListDir))
  Copy-GateFiles $inner
  $r = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $inner 'scripts' 'install-gate.ps1'), '-GitHubUser', 'fixture', '-Denylist', $script:ListFile, '-Gitleaks', $Gitleaks) $inner
  Assert-True ($r.ExitCode -ne 0) 'should abort'
  Assert-Contains $r.Output 'nested inside another git work tree'
  Assert-True (-not (Test-Path (Join-Path $inner '.git' 'hooks' 'pre-commit'))) 'no hook written on abort'
  $cfg = Invoke-Proc 'git' @('-C', $inner, 'config', '--local', '--get', 'aor.denylist')
  Assert-True ($cfg.ExitCode -ne 0) 'no config written on abort'
}
Invoke-Case 'install-refuses-list-inside-worktree-and-empty-list' {
  $fx = New-Fixture -SkipInstall
  $inside = Join-Path $fx.Work 'list.txt'; Copy-Item -LiteralPath $script:ListFile -Destination $inside
  $r = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $fx.Work 'scripts' 'install-gate.ps1'), '-GitHubUser', 'fixture', '-Denylist', $inside, '-Gitleaks', $Gitleaks) $fx.Work
  Assert-True ($r.ExitCode -ne 0) 'should abort'; Assert-Contains $r.Output 'sits inside a git work tree'
  $empty = Join-Path $script:ListDir 'empty2.txt'; [IO.File]::WriteAllText($empty, "# only`n")
  $r2 = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $fx.Work 'scripts' 'install-gate.ps1'), '-GitHubUser', 'fixture', '-Denylist', $empty, '-Gitleaks', $Gitleaks) $fx.Work
  Assert-True ($r2.ExitCode -ne 0) 'should abort'; Assert-Contains $r2.Output 'no usable line'
  $r3 = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $fx.Work 'scripts' 'install-gate.ps1'), '-GitHubUser', 'fixture', '-Denylist', (Join-Path $script:ListDir 'absent.txt'), '-Gitleaks', $Gitleaks) $fx.Work
  Assert-True ($r3.ExitCode -ne 0) 'should abort'; Assert-Contains $r3.Output 'denylist not found'
  Assert-True (-not (Test-Path (Join-Path $fx.Work '.git' 'hooks' 'pre-commit'))) 'no hook written on abort'
}
Invoke-Case 'install-refuses-wrong-scanner-and-hooksPath' {
  $fx = New-Fixture -SkipInstall
  $r = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $fx.Work 'scripts' 'install-gate.ps1'), '-GitHubUser', 'fixture', '-Denylist', $script:ListFile, '-Gitleaks', (Join-Path $fx.Work 'LICENSE')) $fx.Work
  Assert-True ($r.ExitCode -ne 0) 'should abort'; Assert-Contains $r.Output 'does not hash to the pinned release'
  [void](Invoke-Proc 'git' @('-C', $fx.Work, 'config', '--local', 'core.hooksPath', 'elsewhere'))
  $r2 = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $fx.Work 'scripts' 'install-gate.ps1'), '-GitHubUser', 'fixture', '-Denylist', $script:ListFile, '-Gitleaks', $Gitleaks) $fx.Work
  Assert-True ($r2.ExitCode -ne 0) 'should abort'; Assert-Contains $r2.Output 'core.hooksPath is set'
  $r3 = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $fx.Work 'scripts' 'install-gate.ps1'), '-GitHubUser', 'bad login!', '-Denylist', $script:ListFile, '-Gitleaks', $Gitleaks) $fx.Work
  Assert-True ($r3.ExitCode -ne 0) 'should abort'; Assert-Contains $r3.Output 'GitHubUser must be a GitHub login'
}
Invoke-Case 'install-idempotent-and-https-rewrite' {
  $fx = New-Fixture -SkipInstall -OriginUrl 'git@github.com:Example-Org/example.git'
  foreach ($i in 1, 2) {
    $r = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $fx.Work 'scripts' 'install-gate.ps1'), '-GitHubUser', 'fixture', '-Denylist', $script:ListFile, '-Gitleaks', $Gitleaks) $fx.Work
    Assert-True ($r.ExitCode -eq 0) "install run $i should pass: $($r.Output)"
  }
  $url = (Invoke-Proc 'git' @('-C', $fx.Work, 'remote', 'get-url', '--push', 'origin')).Output.Trim()
  Assert-True ($url -eq 'https://github.com/Example-Org/example.git') "push URL rewritten to HTTPS, got $url"
  $raw = (Invoke-Proc 'git' @('-C', $fx.Work, 'config', '--local', '--get-all', 'credential.helper')).Output -replace "`r", ''
  $helpers = [System.Collections.Generic.List[string]]::new()
  foreach ($h in ($raw -split "`n")) { $helpers.Add($h) }
  while ($helpers.Count -gt 0 -and $helpers[$helpers.Count - 1] -eq '') { $helpers.RemoveAt($helpers.Count - 1) }
  $expectedHelper = '!f() { echo username=fixture; echo "password=$(gh auth token --user fixture)"; }; f'
  Assert-True ($helpers.Count -eq 2 -and $helpers[0] -eq '' -and $helpers[1] -eq $expectedHelper) "credential.helper must be exactly the reset entry then the gh-keyring helper after two runs, got $($helpers.Count) entries: $($helpers -join ' / ')"
  foreach ($h in 'pre-commit', 'pre-push') {
    $stub = [IO.File]::ReadAllText((Join-Path $fx.Work '.git' 'hooks' $h))
    Assert-True ($stub.StartsWith("#!/bin/sh`n")) "$h stub starts with #!/bin/sh and LF"
    Assert-True ($stub.Contains('exec pwsh -NoProfile -NonInteractive -File scripts/publish-gate.ps1')) "$h stub execs the tracked gate"
    Assert-True (-not $stub.Contains("`r")) "$h stub has no CR"
  }
  Assert-True ((Invoke-Proc 'git' @('-C', $fx.Work, 'config', '--local', '--get', 'aor.denylist')).Output.Trim() -eq [IO.Path]::GetFullPath($script:ListFile)) 'aor.denylist recorded'
}
Invoke-Case 'get-gitleaks-reports-a-verified-binary-without-downloading' {
  $dest = Split-Path -Parent $Gitleaks
  $r = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $SourceRoot 'scripts' 'get-gitleaks.ps1'), '-Destination', $dest)
  Assert-True ($r.ExitCode -eq 0) "should pass: $($r.Output)"
  Assert-Contains $r.Output 'already present and verified'
  Assert-Contains $r.Output ([IO.Path]::GetFileName($Gitleaks))
}

# ------------------------------------------------------------------------------------------------
# Cases added after the two-lane SME pass (fixtures A, B, C, D as left by the cases above)
# ------------------------------------------------------------------------------------------------

Invoke-Case 'pc-listed-plugin-name-withheld' {
  $listedDir = 'aor-' + $script:List[1].ToLowerInvariant()
  Write-File $A "plugins/$listedDir/skills/s/SKILL.md" "---`nname: s`ndescription: x`n---`nx`n"
  Write-File $A '.claude-plugin/marketplace.json' "{`"name`":`"aor`",`"plugins`":[{`"name`":`"$listedDir`",`"source`":`"./plugins/$listedDir`"}]}`n"
  $r = Expect-CommitRefused $A @('missing plugins/<withheld: contains a listed identifier>/.claude-plugin/plugin.json')
}
Invoke-Case 'pc-gitattributes-diff-override' {
  Write-File $A '.gitattributes' "* text=auto`n*.pyc diff`n"
  [void](Expect-CommitRefused $A @('.gitattributes line 2 sets a diff, binary or filter attribute'))
  Write-File $A '.gitattributes' "* text=auto`n*.md filter=lfs`n"
  [void](Expect-CommitRefused $A @('.gitattributes line 2 sets a diff, binary or filter attribute'))
  Write-File $A 'legacy/.gitattributes' "*.pyc -diff`n"
  [void](Expect-CommitRefused $A @('.gitattributes line 1 sets a diff, binary or filter attribute', 'legacy/.gitattributes'))
}
Invoke-Case 'pc-nested-pattern-config' {
  Copy-Item -LiteralPath (Join-Path $A.Work '.gitleaks.toml') -Destination (Join-Path $A.Work 'scripts' '.gitleaks.toml')
  [void](Expect-CommitRefused $A @('.gitleaks.toml is permitted at the root only: scripts/.gitleaks.toml'))
}
Invoke-Case 'pc-inherited-allowlist-more-classes' {
  Write-File $A 'scripts/javascript.json' "{`"p`":`"$($samples['aor-unresolved-placeholder'])`"}`n"
  [void](Expect-CommitRefused $A @('pattern aor-unresolved-placeholder (own-only)', 'scripts/javascript.json'))
  Write-File $A 'scripts/jquery.min.js' "// $($samples['aor-claude-project-slug'])`n"
  [void](Expect-CommitRefused $A @('pattern aor-claude-project-slug (own-only)', 'scripts/jquery.min.js'))
  Write-File $A 'scripts/vendor/github.com/x/y.go' "// $($script:List[3])`n"
  [void](Expect-CommitRefused $A @('listed identifier in content', 'scripts/vendor/github.com/x/y.go'))
}
Invoke-Case 'pc-submodule-gitlink' {
  $head = (Git $A @('rev-parse', 'HEAD')).Output.Trim()
  $u = Git $A @('update-index', '--add', '--cacheinfo', "160000,$head,scripts/sub")
  Assert-True ($u.ExitCode -eq 0) "update-index: $($u.Output)"
  $r = Git $A @('commit', '-q', '-m', 'gitlink')
  Assert-True ($r.ExitCode -ne 0) 'a submodule entry must be refused'
  Assert-Contains $r.Output 'submodule entries are not permitted'
  Assert-Contains $r.Output 'scripts/sub'
  [void](Git $A @('reset', '-q'))
  Reset-Work $A
}
Invoke-Case 'pc-pattern-in-file-name' {
  Write-File $A ('scripts/' + $samples['aor-claude-project-slug'] + '.md') "clean`n"
  [void](Expect-CommitRefused $A @('pattern aor-claude-project-slug', 'in file names, commit metadata and tag objects'))
}
Invoke-Case 'pp-listed-tag-name-withheld' {
  [void](Git $B @('tag', "$($script:List[2])--v0.1.0"))
  try { [void](Expect-PushRefused $B @("refs/tags/$($script:List[2])--v0.1.0") @('tag <withheld: contains a listed identifier> names a plugin that has no manifest')) }
  finally { [void](Git $B @('tag', '-d', "$($script:List[2])--v0.1.0")) }
}
Invoke-Case 'pp-annotated-tag-message-literal' {
  $v = ((Git $B @('show', 'HEAD:plugins/aor-t/.claude-plugin/plugin.json')).Output | ConvertFrom-Json).version
  [void](Git $B @('tag', '-a', '-m', "release notes mention $($script:List[3])", "aor-t--v$v"))
  try { [void](Expect-PushRefused $B @("refs/tags/aor-t--v$v") @('listed identifier in the tagger or message of 1 tag object(s)')) }
  finally { [void](Git $B @('tag', '-d', "aor-t--v$v")) }
}
Invoke-Case 'pp-pattern-in-commit-message' {
  Write-File $B 'scripts/msg.md' "clean`n"
  $c = Commit $B "saved under $($samples['aor-windows-user-path']) today"
  Assert-True ($c.ExitCode -eq 0) 'pre-commit does not see the message'
  [void](Expect-PushRefused $B @('main') @('pattern aor-windows-user-path', 'in file names, commit metadata and tag objects'))
  Reset-ToRemote $B
}
Invoke-Case 'pp-range-interior-binary' {
  Write-Bytes $B 'scripts/x.pyc' ([byte[]](0..255))
  [void](Commit $B 'binary' -NoVerify)
  Remove-Item -Force -LiteralPath (Join-Path $B.Work 'scripts' 'x.pyc')
  [void](Commit $B 'removed again')
  $r = Expect-PushRefused $B @('main') @('binary file: scripts/x.pyc')
  Assert-Contains $r.Output ' tree: binary file'
  Reset-ToRemote $B
}
Invoke-Case 'ci-created-listed-name-in-history' {
  Write-File $C "scripts/$($script:List[0]).md" "x`n"
  [void](Commit $C 'named' -NoVerify)
  Remove-Item -Force -LiteralPath (Join-Path $C.Work 'scripts' "$($script:List[0]).md")
  [void](Commit $C 'removed')
  $r = Invoke-CiGate $C 'push' 'refs/heads/main' @{ before = '0000000000000000000000000000000000000000'; created = $true; forced = $false } $ciList
  Assert-True ($r.ExitCode -ne 0) 'should fail on the historical file name'
  Assert-Contains $r.Output 'listed identifier in 1 file name(s)'
  Assert-NoListedLiteral $r.Output
  Reset-ToRemote $C
}
Invoke-Case 'install-removes-pushurl' {
  $fx = New-Fixture -SkipInstall -OriginUrl 'https://github.com/Example-Org/example.git'
  [void](Invoke-Proc 'git' @('-C', $fx.Work, 'config', '--local', 'remote.origin.pushurl', 'git@github.com:Example-Org/example.git'))
  $r = Invoke-Proc 'pwsh' @('-NoProfile', '-NonInteractive', '-File', (Join-Path $fx.Work 'scripts' 'install-gate.ps1'), '-GitHubUser', 'fixture', '-Denylist', $script:ListFile, '-Gitleaks', $Gitleaks) $fx.Work
  Assert-True ($r.ExitCode -eq 0) "install should pass: $($r.Output)"
  $push = (Invoke-Proc 'git' @('-C', $fx.Work, 'remote', 'get-url', '--push', 'origin')).Output.Trim()
  Assert-True ($push -eq 'https://github.com/Example-Org/example.git') "push URL must be the HTTPS URL, got $push"
}

Invoke-Case 'adhoc-listed-basename-withheld' {
  $dir = 'aor-' + $script:List[1].ToLowerInvariant()
  Write-File $D "incubator/$dir/.claude-plugin/plugin.json" "{`"name`":`"$dir`",`"version`":`"0.1.0`",`"author`":{`"name`":`"f`"},`"license`":`"MIT`"}`n"
  Write-File $D "incubator/$dir/skills/aor-w-skill/SKILL.md" "---`nname: aor-w-skill`ndescription: x`n---`nx`n"
  [void](Git $D @('add', 'incubator'))
  $r = Gate $D @('-Path', (Join-Path $D.Work 'incubator' $dir))
  Assert-True ($r.ExitCode -ne 0) 'the file names carry the token, so the run must fail'
  Assert-Contains $r.Output 'ad-hoc on <withheld: contains a listed identifier> as plugins/<withheld: contains a listed identifier>/'
  Assert-Contains $r.Output 'listed identifier in 2 file name(s)'
  Assert-NoListedLiteral $r.Output
  Reset-Work $D
}
# ------------------------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------------------------

$passed = @($script:Results | Where-Object Passed).Count
$failed = @($script:Results | Where-Object { -not $_.Passed }).Count
Write-Host ''
Write-Host ("suite: {0} case(s) = {1} passed + {2} failed; {3} ms" -f $script:Results.Count, $passed, $failed, ($script:Results | Measure-Object -Property Ms -Sum).Sum)
foreach ($r in ($script:Results | Where-Object { -not $_.Passed })) { Write-Host "  FAIL $($r.Name): $($r.Detail)" }

if (-not $KeepFixtures) {
  try {
    Get-ChildItem -LiteralPath $script:Root -Recurse -Force -File | ForEach-Object { $_.Attributes = 'Normal' }
    Remove-Item -LiteralPath $script:Root -Recurse -Force
  } catch { Write-Host "suite: could not remove $($script:Root): $($_.Exception.GetType().Name)" }
} else { Write-Host "suite: fixtures kept at $($script:Root)" }

exit $(if ($failed -eq 0 -and $script:Results.Count -gt 0) { 0 } else { 1 })
