# Agents-On-Rails skills

The public home of the `aor-*` agent skills: one repository that users of Claude Code and GitHub
Copilot CLI add as an install source, and that `gh skill` and `npx skills` install from directly,
with every skill family shipped as a plugin under `plugins/` and versioned on its own.

The marketplace is named `aor`. One plugin ships today, as a `0.x` preview:

| Plugin | Version | Skills | Needs |
|---|---|---|---|
| `aor-comm` | 0.1.0 | `aor-format-teams-message`: drafts a rich Microsoft Teams message and puts it on the clipboard ready to paste | Windows; Python 3.9 or later on PATH as `python` |

## Install

### Claude Code

```
claude plugin marketplace add Agents-On-Rails/skills
claude plugin install aor-comm@aor
```

Inside a session the same two steps are `/plugin marketplace add Agents-On-Rails/skills` and
`/plugin install aor-comm@aor`. The first command clones over SSH, so it needs an SSH key
registered with GitHub; without one, set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` first and it clones
over HTTPS. Plugin skills are namespaced, so the skill is `/aor-comm:aor-format-teams-message`.

Claude Code installs the version named in the plugin's manifest and updates only when that version
rises. To pick up a new version, run `claude plugin marketplace update aor` and then
`claude plugin update aor-comm@aor`, and restart. Marketplaces you add yourself do not auto-update
unless you enable it for them. To install a fixed release instead of the latest, add the marketplace
at its tag before installing: `claude plugin marketplace add Agents-On-Rails/skills@aor-comm--v0.1.0`,
then `claude plugin install aor-comm@aor`.

### GitHub Copilot CLI

```
copilot plugin marketplace add Agents-On-Rails/skills
copilot plugin install aor-comm@aor
```

Copilot installs the tree at the tip of `main`, and `copilot plugin update aor-comm@aor` refreshes
to it whether or not the version has changed.

### gh skill

```
gh skill install Agents-On-Rails/skills aor-format-teams-message
```

Installs the skill under its bare name for the agent you choose (`--agent`, `--scope`), from the tip
of `main` while this repository has no full GitHub Release. `gh skill update --all` refreshes it.
To install a release, pin it: `--pin aor-comm--v0.1.0`, or name it as
`aor-format-teams-message@aor-comm--v0.1.0`; a pinned install is left alone by `gh skill update`
until `--unpin`.

### npx skills

```
npx skills add Agents-On-Rails/skills --skill aor-format-teams-message
```

Follows the marketplace entries; `npx skills add Agents-On-Rails/skills --list` shows them, and
`-a <agent> -y` runs without prompts.

## Versions and releases

Each plugin carries one `version`, in its `plugin.json`, and every push that changes a plugin raises
it. A release is that version plus the tag `<plugin>--v<version>` (`aor-comm--v0.1.0`), pushed
together. While a plugin is `0.x`, its releases are tags only; a GitHub Release, if any, is marked
as a prerelease.

## Layout

- `plugins/aor-<family>/` holds one plugin per skill family, each with its own
  `.claude-plugin/plugin.json` and `skills/<name>/SKILL.md`; the marketplace manifest at
  `.claude-plugin/marketplace.json` lists them.
- `legacy/<skill>/` holds the first-generation requirements skills (`aor-req`, `aor-test-trace`,
  `aor-review`, `aor-review-adhoc`) and their SME agents under `legacy/agents/`, parked untouched
  until they are ported into a family. They are not installable through a plugin. The tag
  `v0.1.0-preview` keeps the original layout and the documents that described it.
- `scripts/` holds the publish-safety gate, its installer, the scanner fetcher and the fixture suite.
- `.github/workflows/gate.yml` runs the same gate as detection in CI, the fixture suite on Windows
  and Linux, and the two root validators.

## The publish-safety gate

Everything in this repository is public, and it is maintained from a context that also holds
private material. The gate is what keeps the two apart, and it is structural rather than
editorial: a commit or push that carries the wrong thing is refused, not flagged for later.

One script, `scripts/publish-gate.ps1`, runs in three places: as the local pre-commit hook over
the staged tree, as the local pre-push hook over every commit in the push range, and in CI over
the pushed range. It uses two instruments. The literal layer runs `git grep -F` over a private
list of identifiers the maintainer must never publish; the list lives outside every repository
(its path in the clone's local git config) and reaches CI as an organisation secret read through
stdin, the gate refuses to run without it, and it never prints a matched value. The pattern layer
runs a hash-pinned release of [gitleaks](https://github.com/gitleaks/gitleaks) with the tracked
`.gitleaks.toml`, which extends gitleaks' default secret rules with this repository's own rules
for path shapes that would reveal a machine or an account; the config file is the only place
those patterns are spelled. Tree rules make process noise impossible to commit: only a fixed set
of entries may exist at the root, file names that look like session artifacts are refused, every
`SKILL.md` must sit at `plugins/aor-<family>/skills/<name>/SKILL.md` or `legacy/<name>/SKILL.md`
with a frontmatter `name` equal to its directory and unique across the tree, every plugin
directory must carry a complete manifest and exactly one marketplace entry, no file may exceed
1 MB, and no binary file is accepted. The noise, size and binary rules also run over every commit
tree inside a push range, not only its tip, and file names, commit messages, authors and tag
messages pass through both the identifier list and the pattern rules. Every run first proves its
own detection with a positive control and re-hashes the scanner; an error is a failure, never an
empty result.

Pushes to `main` must raise the `version` of every plugin they touch; a tag must be named
`<plugin>--v<version>`, match that plugin's manifest at the tagged commit and be reachable from
`main`; non-fast-forward pushes to `main` and tag deletions are refused outside INCIDENT MODE.
Branch protection on `main` blocks force pushes and deletions as a second lock.

### On a hit

The gate prints the path, the line numbers and the number of matching lines, never the matched
text. The maintainer re-derives the culprit locally with `git grep -n`. A file whose name
contains a listed identifier is reported only as a count, and the name is withheld.

### Setting up a clone (maintainers)

1. Have PowerShell 7 (`pwsh`), git 2.28 or later, the GitHub CLI (`gh`, signed in) and Claude Code (a native install or the npm package) on PATH.
2. Write the identifier list by hand, outside every repository: one identifier per line, `#`
   comment lines allowed, UTF-8. The default location is `~/.aor/publish-denylist.txt`.
3. Fetch the pinned scanner: `pwsh scripts/get-gitleaks.ps1`. It verifies the download against
   the hashes pinned in the gate script and refuses anything else.
4. Install: `pwsh scripts/install-gate.ps1 -GitHubUser <your login>`. In one act it checks that
   the clone is not nested inside another work tree, that the list and the scanner are in place,
   writes the two hooks, records the list and scanner paths in local git config, forces the
   remote URL to HTTPS and sets a credential helper that reads your token from the gh keyring.
   It writes nothing if any check fails, and it is safe to re-run.

From then on every commit and push from that clone runs the gate. Bypassing it (`--no-verify`,
editing the local config) is a deliberate, visible act; the threat model is accidental exposure
by the maintainer's own tools, not a hostile maintainer.

To check a plugin directory that lives elsewhere, before it moves here:
`pwsh scripts/publish-gate.ps1 -Path <directory>` treats it as `plugins/<basename>/` and applies
the same rules (without the root set and the marketplace check), reading the list and scanner
paths from that repository's local config.

### Exceptions

The literal layer has none, in any file, name or message. The pattern layer accepts only scoped
entries in `.gitleaks.toml`, each with a description naming its reason, never a content literal
and never a `.gitleaksignore`. The gate pins the number of such entries, so adding one is a
two-file commit that names the entry, confirmed by the maintainer. There are none today. The
pattern config is accepted at the root only, a gitleaks ignore file nowhere, and a .gitattributes
line may not change how git tells text from binary or add a filter, because either would change
what the gate sees.

### If the CI gate fails on content that is already on the remote

1. **Freeze.** No further pushes until the rest is done.
2. **Remove.** Rewrite the ref so that no reachable commit carries the content; delete or move
   every tag that points at a removed commit in the same push; delete any GitHub Release on such
   a tag. The hooks accept a non-fast-forward push to `main` and a tag deletion only with
   `AOR_GATE_INCIDENT=1` set for that one push, and they still scan the whole new range. Lift
   branch protection for that push and reinstate it afterwards. Never use `--no-verify`.
3. **Purge.** Ask GitHub Support, through their documented request, to remove cached views and
   pull-request references. Forks and clones made inside the window are out of reach; record
   them as such.
4. **Rotate.** Anything that is a secret is rotated. Identifiers cannot be rotated; the exposure
   window is recorded.
5. **Reinstate.** Confirm branch protection is back and re-run the CI gate green.

### Pull requests from forks

CI withholds the organisation secret from a fork, so the literal layer reports "denylist not
loaded" and the `gate` job is red by design, while its pattern and tree layers still report. A
maintainer reviews the diff, fetches the branch, merges it on `main` through the local hooks and
pushes. The merge button is never on the path.

### Scanner cost on Windows

The scanner is an unsigned binary in the user profile. Windows Defender may re-scan it on every
launch, which can add a second or two to each commit. That is accepted; a system-wide install on
PATH would trade the hash pin for speed.

## License

MIT, see `LICENSE`.
