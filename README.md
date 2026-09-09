# Agents-On-Rails skills

The public home of the `aor-*` agent skills: one repository that users of Claude Code and GitHub
Copilot CLI add as an install source, and that `gh skill` and `npx skills` install from directly
where a skill is self-contained, with every skill family shipped as a plugin under `plugins/` and
versioned on its own.

Maintained by Troels Vognsen (<agentsonrails@trvo.dk>) as a personal open-source project.
`Agents-On-Rails` is a GitHub organisation used for that work; there is no company behind it and no
support commitment. To report a security problem, see [`.github/SECURITY.md`](.github/SECURITY.md);
for anything else, open an issue.

The marketplace is named `aor`. Two plugins ship today, each as a `0.x` preview:

| Plugin | Version | Skills | Needs |
|---|---|---|---|
| `aor-comm` | 0.1.4 | `aor-format-teams-message`: drafts a rich Microsoft Teams message and puts it on the clipboard ready to paste | Windows; Python 3.9 or later on PATH as `python` |
| `aor-kb` | 0.1.5 | `aor-kb-query`: searches a knowledge base and returns only claims that clear a trust grade, each stamped with its label. `aor-kb-capture`: records what a session learned as a graded claim and routes it to a personal or a work base. Routing is fail-closed but **not a boundary guarantee** — it never reads the claim's content and cannot tell work knowledge from personal, it takes its signal from the working directory the command runs in, and its signal lists are empty until you fill them | Windows; Python 3.9 or later on PATH as `python`, plus the pinned `strictyaml` in the plugin's `requirements.txt` |

## Before you install

**What these skills do on your machine.** Both plugins are Python that runs locally when you invoke
the skill. Neither makes any network call: there is no network-capable import anywhere in either
plugin's code. `aor-comm` reads only the files you name on the command line, writes nothing to disk,
and replaces the clipboard. `aor-kb` reads and writes a knowledge base you point it at, and creates
a small configuration directory under your local application data on first use.

**The clipboard is emptied before it is written.** `aor-comm` clears the clipboard and then writes
the new contents. If the write fails part-way, the clipboard is already empty — whatever was on it
before is gone. Do not run it holding something you cannot regenerate.

**An unpinned install follows this repository.** On the channels that do not pin — Copilot CLI,
which tracks the tip of `main`, and `gh skill` or `npx skills` without a pin — the next update runs
whatever has been committed here since, with the file-system and clipboard access you gave the
skill. Pin a tag if that is not what you want; each install section below says how.

**What a pin does and does not give you.** A pinned tag cannot be moved: repository rulesets refuse
tag deletion and non-fast-forward tag pushes, and the publish gate refuses to repoint a tag the
remote already carries. That is immutability. It is **not** authenticity — nothing in this
repository is signed, every commit reports `verified=false reason=unsigned`, and the tag objects
carry no signature. Pinning means "the same bytes as last time"; it does not mean "the bytes came
from the maintainer". If you need that, review the diff yourself.

**Windows only.** Both skills call Windows APIs — `aor-comm` writes the clipboard through `user32`,
`aor-kb` resolves its configuration directory through a Windows shell API and refuses to import
elsewhere by design. They do not run on macOS or Linux. The maintainer tooling in `scripts/` is
Windows and Linux only and throws on anything else.

## Install

### Which channel gives you what

The same skill has a different name depending on how you installed it, and only the plugin channels
carry `aor-kb`.

| Channel | Command shape | Installed as | How you invoke it |
|---|---|---|---|
| Claude Code plugin | `claude plugin install aor-comm@aor` | plugin, namespaced | `/aor-comm:aor-format-teams-message` |
| Copilot CLI plugin | `copilot plugin install aor-comm@aor` | plugin, bare name when unique | you describe the task; Copilot matches on the skill's description rather than a typed command |
| `gh skill` | `gh skill install Agents-On-Rails/skills aor-format-teams-message` | bare name, flat per-scope directory | the bare skill name, per your agent's convention |
| `npx skills` | `npx skills add Agents-On-Rails/skills --skill aor-format-teams-message` | bare name under `.claude/skills/` in the current project | the bare skill name, per your agent's convention |

The double prefix in `/aor-kb:aor-kb-query` is deliberate: the plugin is `aor-kb` and the skill is
`aor-kb-query`, so that the bare name stays globally unique on the channels that install it flat.

**If two channels install the same skill on one machine you get two copies on separate update
lines**, and which one answers depends on the host:

- **Claude Code** refuses a second plugin that declares a name an installed plugin already owns, and
  names the remedy. A personal skill of the same bare name shadows the plugin's bare alias.
- **Copilot CLI** resolves first-found-wins across its discovery tiers, with plugin skills seventh of
  eight. A plugin skill that loses is **silently ignored** — no message, no error.
- **`gh skill`** refuses a same-named skill from a second repository unless you pass `--force`, which
  overwrites. The two never coexist.

Pick one channel per machine unless you have a reason not to.

### Claude Code

```
claude plugin marketplace add Agents-On-Rails/skills
claude plugin install aor-comm@aor
claude plugin install aor-kb@aor
```

Inside a session the same steps are `/plugin marketplace add Agents-On-Rails/skills` and
`/plugin install aor-comm@aor` or `/plugin install aor-kb@aor`.

- **Name after install** — plugin skills are namespaced: `/aor-comm:aor-format-teams-message`,
  `/aor-kb:aor-kb-query` and `/aor-kb:aor-kb-capture`.
- **Transport** — the first command clones over SSH, so it needs an SSH key registered with GitHub.
  Without one, set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` first and it clones over HTTPS.
- **Updates** — Claude Code installs the version named in the plugin's manifest and updates only
  when that version rises, so a commit here that does not raise the version never reaches you. Run
  `claude plugin marketplace update aor`, then `claude plugin update aor-comm@aor`, then restart.
  Marketplaces you add yourself do not auto-update unless you enable it for them.
- **To pin** — add the marketplace at a tag before installing:
  `claude plugin marketplace add Agents-On-Rails/skills@aor-comm--v0.1.0`, then
  `claude plugin install aor-comm@aor`. A marketplace pins to a branch or a tag, never to a commit.
- **Confirm it worked** — restart, then `claude plugin list`. You should see `aor-comm@aor` with
  status `✔ enabled` and the version you expect. A running session keeps resolving the copy it
  started with, so the restart is not optional and `plugin list` in a new terminal says nothing
  about a session already open.

### GitHub Copilot CLI

```
copilot plugin marketplace add Agents-On-Rails/skills
copilot plugin install aor-comm@aor
copilot plugin install aor-kb@aor
```

- **Name after install** — the bare skill name, `aor-format-teams-message`. If another plugin ever
  ships the same skill name, both appear plugin-qualified as `aor-comm:aor-format-teams-message`.
  Copilot has no user-typed slash commands: the skill is selected by matching your request against
  its description, so you describe the task rather than naming the skill.
- **Transport** — a public marketplace needs no login step.
- **Updates** — Copilot installs the tree at the tip of `main`, and
  `copilot plugin update aor-comm@aor` refreshes to it whether or not the version has changed. For
  Copilot the tracked branch is the release line and the version string is informational.
- **To pin** — `copilot plugin marketplace add Agents-On-Rails/skills#aor-comm--v0.1.0` is accepted
  and the ref is recorded in the profile settings. Its effect on a subsequent install has not been
  confirmed here, so treat Copilot as an unpinned channel unless you have verified otherwise
  yourself.
- **Confirm it worked** — start a **new** session, then run `copilot skill list`. Copilot enumerates
  skills once at session start, so a skill added to a running session will not appear in it and the
  skill tool will answer "not found" even though the install succeeded.

### gh skill

```
gh skill install Agents-On-Rails/skills aor-format-teams-message
```

**`aor-kb-query` and `aor-kb-capture` are not offered this way — install `aor-kb` as a plugin
instead** (`claude plugin install aor-kb@aor` or `copilot plugin install aor-kb@aor`, above). A
skill-level install delivers a skill's own folder and nothing above it. Both `aor-kb` skills share
their `tools/`, `requirements.txt` and `instances.yml.example` at the **plugin** root, so a
skill-level install places a `SKILL.md` whose tool is not there. **It looks like it worked**: the
command exits 0, prints no warning, and writes exactly one file. `aor-format-teams-message` keeps
its tool beside its own `SKILL.md`, which is why it installs cleanly this way.

- **Name after install** — the bare skill name, in a flat per-scope directory. Names must therefore
  be unique across every repository you install from, not just within this one.
- **Placement** — `--scope` takes `project` or `user` and **defaults to `project`**. `--agent`
  selects the host directory; the values you are likely to want are `claude-code` and
  `github-copilot`, and `gh skill install --help` lists all of them. Run non-interactively with no
  `--agent`, it defaults to `github-copilot`. `--dir` overrides both.
- **Updates** — `gh skill update --all` refreshes installed skills; `--dry-run` reports what would
  change without touching anything.
- **To pin** — `--pin aor-comm--v0.1.0`, or name it as
  `aor-format-teams-message@aor-comm--v0.1.0`. A pin takes a tag or a commit SHA, and a pinned
  install is left alone by `gh skill update` until `--unpin`.
- **Unpinned resolution is repository-wide, and that will surprise you.** An unpinned install
  resolves to the newest full GitHub Release anywhere in this repository, not to the newest tag and
  not to the skill's own tag; plain tags are ignored and prereleases do not count. While this
  repository has no full Release, that means the tip of `main`. The moment one exists — for either
  plugin — every unpinned install of every skill here resolves to it. Pin if that matters to you.
- **Confirm it worked** — `gh skill` has no `list` command. Look in the directory the install
  reported and check the skill's folder is there with its `SKILL.md` **and its tool files**; a
  one-file result is the silent failure described above. On Windows the success output may end with
  `(could not read directory)` even when every file is present — that message is a false alarm, not
  a failed install.

### npx skills

```
npx skills add Agents-On-Rails/skills --skill aor-format-teams-message
```

**`aor-kb-query` and `aor-kb-capture` are not offered this way either** — same reason as the
`gh skill` section above, and the same successful-looking one-file result. Install `aor-kb` as a
plugin.

- **Name after install** — the bare skill name. With `-a claude-code` it lands in
  `.claude/skills/<name>/` **inside the current project**, not in your home directory; `-g` installs
  globally instead.
- **Placement** — `-a` takes an agent identifier, and the identifier for Claude Code is
  `claude-code`; `-a claude` is rejected. `npx skills add Agents-On-Rails/skills --list` shows what
  this repository offers, and `npx skills list` shows what you have installed. By default the
  installer **symlinks** into the agent directory; `--copy` copies instead.
- **Updates** — `npx skills update`. There is no pin flag: this channel follows the repository, so
  treat it as unpinned. It writes a `skills-lock.json` beside the install, and
  `npx skills experimental_install` restores from it.
- **Telemetry** — the installer has an install telemetry event, which its `--metadata` flag attaches
  JSON to. That is the installer's behaviour, not this repository's, and it is worth knowing before
  you run it in a place where that matters.
- **Confirm it worked** — `npx skills list`, and check the skill's folder holds its tool files and
  not a lone `SKILL.md`.

### When these notes were taken

Everything above about how the four installers behave was observed on Windows on **2026-09-09**
against Claude Code 2.1.266, GitHub Copilot CLI 1.0.83, GitHub CLI 2.93.0 and `skills@latest` under
npx 11.12.1 / Node 26. `gh skill` is a GitHub CLI **preview** and is documented as subject to change
without notice; Copilot CLI has moved through eleven point releases in the seven weeks these notes
were built from. Where an installer's behaviour has changed since, its own `--help` wins over this
page.

## Versions and releases

Each plugin carries one `version`, in its `plugin.json`, and every push that changes a plugin raises
it. A release is that version plus the tag `<plugin>--v<version>` (`aor-comm--v0.1.0`), pushed
together. While a plugin is `0.x`, its releases are tags only; a GitHub Release, if any, is marked
as a prerelease.

**What `0.x` means here.** Anything may change in any release: skill names, invocation strings,
command-line flags and file locations. There is no deprecation period, no migration path and no
support commitment. If you need a fixed version, pin a tag — each install section above says how for
its channel. None of these plugins is close to 1.0.

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
- `.github/SECURITY.md` and `.github/CONTRIBUTING.md` carry the disclosure path and the contribution
  terms.

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
Two repository rulesets are the second lock: one on the default branch and one on all tags, each
refusing deletion and non-fast-forward pushes. Note that these are **rulesets**, not classic branch
protection — the classic branch-protection API reports this branch as unprotected, and checking
there will mislead you. Both rulesets allow organisation and repository admins to bypass them, so
they guard against accident rather than against the maintainer; that matches the threat model stated
below.

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

**Maintaining requires Windows or Linux.** The scanner fetcher and the gate both resolve a pinned
binary for Windows x64 or Linux x64 and throw on anything else, so the gate cannot run on macOS and
CI runs neither macOS nor any arm64 host. The platform test looks at the operating system only, so
on an arm64 Windows or Linux machine the fetch takes the x64 branch and fails later at the hash
comparison rather than at a clear platform error.

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
   `AOR_GATE_INCIDENT=1` set for that one push, and they still scan the whole new range. Disable the
   branch and tag rulesets for that push and re-enable them afterwards. Never use `--no-verify`.
3. **Purge.** Ask GitHub Support, through their documented request, to remove cached views and
   pull-request references. Forks and clones made inside the window are out of reach; record
   them as such.
4. **Rotate.** Anything that is a secret is rotated. Identifiers cannot be rotated; the exposure
   window is recorded.
5. **Reinstate.** Confirm both rulesets are active again and re-run the CI gate green.

### Pull requests from forks

CI withholds the organisation secret from a fork, so the literal layer reports "denylist not
loaded" and the `gate` job is red by design, while its pattern and tree layers still report. A
maintainer reviews the diff, fetches the branch, merges it on `main` through the local hooks and
pushes. The merge button is never on the path. See [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md).

### Scanner cost on Windows

The scanner is an unsigned binary in the user profile. Windows Defender may re-scan it on every
launch, which can add a second or two to each commit. That is accepted; a system-wide install on
PATH would trade the hash pin for speed.

## License

MIT, see `LICENSE`.
