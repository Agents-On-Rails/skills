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
the skill. Neither makes a network call: no module in either plugin imports anything
network-capable, and the only subprocess either one starts is `git`, run against a local repository
you named — the subcommands it uses are all local ones. `aor-kb` additionally imports `strictyaml`,
a third-party package you install yourself; that is outside this repository and its behaviour is its
own.

- **`aor-comm`** reads the files you name on the command line, plus one file it ships with —
  `capabilities.json`, its own manifest, read from its install directory on every render. It writes
  nothing to disk at all, and it replaces the clipboard.
- **`aor-kb`** reads and writes a knowledge base you point it at. Two things worth knowing before
  you install it. **A query is logged by default:** unless you pass `--no-log`, each query appends
  a line to `<your-kb>/_serve/serve-log.jsonl` recording the time, **the full command line you
  typed**, and the id of every claim it returned. And it writes a small configuration directory
  under your local application data — created when you run `kb-capture init` or when a capture
  first records a workspace, not merely by querying.

**The clipboard is emptied before it is written.** `aor-comm` clears the clipboard and then writes
the new contents. If the write fails part-way, the clipboard is already empty — whatever was on it
before is gone. Do not run it holding something you cannot regenerate.

**An unpinned install follows this repository.** On the channels that do not pin — Copilot CLI,
which tracks the tip of `main`, and `gh skill` or `npx skills` without a pin — the next update runs
whatever has been committed here since, with the file-system and clipboard access you gave the
skill. **Not every channel can pin.** Claude Code and `gh skill` can, and their sections say how;
Copilot CLI accepts a pin syntax whose effect on install is unconfirmed here; `npx skills` has no
pin at all. If you need a fixed version, use Claude Code or `gh skill --pin`.

**What a pin does and does not give you.** A pinned tag cannot be moved: repository rulesets refuse
tag deletion and non-fast-forward tag pushes, and the publish gate refuses to repoint a tag the
remote already carries. That is immutability. It is **not** authenticity — nothing in this
repository is signed, every commit reports `verified=false reason=unsigned`, and the tag objects
carry no signature. Pinning means "the same bytes as last time"; it does not mean "the bytes came
from the maintainer". If you need that, review the diff yourself.

**Windows only.** Both plugins target Windows and neither is supported anywhere else.
`aor-comm` writes the clipboard through `user32` and cannot work off Windows at all. `aor-kb`
resolves its configuration directory through a Windows shell API that reads the process token, and
raises on any other platform rather than falling back to an environment variable — so
`aor-kb-capture`, and any query that names an instance, refuse immediately off Windows. (A query
given an explicit path never reaches that guard and may import elsewhere; that is an accident of
where the check sits, not a supported configuration.) The maintainer tooling in `scripts/` runs on
Windows and Linux and throws on anything else.

## Install

### What you need first

**Python, reachable as `python`.** Both plugins run Python 3.9 or later and invoke it as `python`,
so that name must resolve to a real interpreter. Check with `python --version`. On Windows a fresh
machine often resolves `python` to the Microsoft Store stub, which opens the Store instead of
running anything — if `python --version` opens the Store or prints nothing, install Python and make
sure it precedes the stub on PATH.

**`aor-kb` needs one more package, and will not run without it.** `aor-kb` depends on
`strictyaml`, pinned:

```
python -m pip install strictyaml==1.7.3
```

Install it into the same interpreter `python` resolves to. Without it, the first invocation of
either `aor-kb` skill exits 3 and names what is missing — it does not fail at install time, and
**no plugin-manager check will tell you**, so do this before you try the skill. The pin is the one
in the plugin's own `requirements.txt`; if it has moved, that file wins over this page.
`aor-comm` has no third-party dependency and needs nothing here.

**Per channel**, in addition:

| Channel | Also needs |
|---|---|
| Claude Code | Claude Code itself, and an SSH key registered with GitHub unless you set the HTTPS variable below |
| Copilot CLI | GitHub Copilot CLI |
| `gh skill` | GitHub CLI new enough to carry `gh skill`, which is a preview subcommand — `gh skill --help` tells you whether you have it |
| `npx skills` | Node and npx |

### Which channel gives you what

The same skill has a different name depending on how you installed it, and only the plugin channels
carry `aor-kb`.

| Channel | Command shape | Installed as | How you invoke it |
|---|---|---|---|
| Claude Code plugin | `claude plugin install aor-comm@aor` | plugin, namespaced | `/aor-comm:aor-format-teams-message` |
| Copilot CLI plugin | `copilot plugin install aor-comm@aor` | plugin, bare name when unique | you describe the task; Copilot matches on the skill's description rather than a typed command |
| `gh skill` | `gh skill install Agents-On-Rails/skills aor-format-teams-message --agent <agent> --scope <scope>` | bare name, in the directory `--agent` and `--scope` select | the bare skill name, per your agent's convention |
| `npx skills` | `npx skills add Agents-On-Rails/skills --skill aor-format-teams-message -a <agent>` | bare name, in the directory `-a` selects — with `-a claude-code`, `.claude/skills/` in the current project | the bare skill name, per your agent's convention |

The double prefix in `/aor-kb:aor-kb-query` is deliberate: the plugin is `aor-kb` and the skill is
`aor-kb-query`, so that the bare name stays globally unique on the channels that install it flat.

**If two channels install the same skill on one machine you get two copies on separate update
lines**, and which one answers depends on the host:

- **Claude Code** refuses a second plugin that declares a name an installed plugin already owns, and
  names the remedy. A personal skill of the same bare name shadows the plugin's bare alias.
- **Copilot CLI** resolves first-found-wins across its discovery tiers, and plugin skills rank below
  every project and personal skills directory. So a copy installed by `gh skill` or `npx skills`
  beats the plugin's, and the plugin's is **silently ignored** — no message, no error.
  `copilot skill list` is what shows you which one won.
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
  ⚠ **`✔ enabled` means loaded, not working.** For `aor-kb` it will say `✔ enabled` even when
  `strictyaml` is missing and every invocation exits 3. The only check that covers that is to run
  the skill — invoke `/aor-kb:aor-kb-query` once and see it answer. Do the dependency step above
  first and this will not arise.

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
- **Confirm it worked** — run `copilot skill list`. It runs in its own process and reads the
  installed set directly, so it tells you the install landed.
  ⚠ **Then start a new session before using the skill.** Copilot enumerates skills once at session
  start, so a session that was already open when you installed will answer "not found" no matter
  what `skill list` says. The two checks answer different questions: `skill list` tells you the
  files are there, a new session is what makes them reachable.
  ⚠ **Being listed means loaded, not working.** As on Claude Code, an `aor-kb` whose `strictyaml`
  is missing lists normally and exits 3 when invoked. Do the dependency step above, then ask
  Copilot to do something the skill covers and see it answer.

### gh skill

```
gh skill install Agents-On-Rails/skills aor-format-teams-message --agent claude-code --scope user
```

**Name the agent and the scope.** Run non-interactively without them, `gh skill` defaults to
`--agent github-copilot` and `--scope project`, so the command lands the skill somewhere you may not
have meant. Substitute `--agent github-copilot` for Copilot, or drop `--scope user` to install into
the current repository instead of your home directory.

**Do not install `aor-kb-query` or `aor-kb-capture` this way — install `aor-kb` as a plugin
instead** (`claude plugin install aor-kb@aor` or `copilot plugin install aor-kb@aor`, above).
⚠ **The installer will offer them to you anyway**: they are declared in the marketplace manifest, so
`--list` shows all three skills and an install of either will appear to succeed. It does not. A
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
- **Unpinned resolution is repository-wide, and that will surprise you.** `gh skill install --help`
  states the order as "latest tagged release in the repository, else default branch HEAD". Measured
  here, "tagged release" means a **GitHub Release**, not a git tag: plain tags are ignored and
  prereleases do not count. This repository has twelve tags and exactly one Release,
  `v0.1.0-preview`, which is marked prerelease — so today an unpinned install resolves to the tip of
  `main`, not to any of those tags. **The first non-prerelease Release, for either plugin, would
  become the resolution target for every unpinned install of every skill here**, because the search
  is repository-wide rather than per-skill. Pin if that matters to you.
- **Confirm it worked** — `gh skill` has no `list` command. Look in the directory the install
  reported and check the skill's folder is there with its `SKILL.md` **and its tool files**; a
  one-file result is the silent failure described above. On Windows the success output may end with
  `(could not read directory)` even when every file is present — that message is a false alarm, not
  a failed install.

### npx skills

```
npx skills add Agents-On-Rails/skills --skill aor-format-teams-message -a claude-code
```

**Name the agent.** Without `-a` the installer prompts, and the identifier for Claude Code is
`claude-code` — `-a claude` is rejected. Add `-y` to skip the prompts entirely, and `-g` to install
globally rather than into the current project.

**Do not install `aor-kb-query` or `aor-kb-capture` this way either** — same reason as the
`gh skill` section above, and the same successful-looking one-file result. `--list` will show all
three skills here too. Install `aor-kb` as a plugin.

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
against Claude Code 2.1.266, GitHub Copilot CLI 1.0.83, GitHub CLI 2.93.0, and the `skills` npm
package 1.5.25 run through npm/npx 11.12.1 on Node 26. **`skills@latest` is a moving target** — the
command this page gives you resolves whatever is newest at the time you run it, which may not be
1.5.25. `gh skill` is a GitHub CLI **preview** and is documented as subject to change
without notice; Copilot CLI released point versions roughly weekly over the period these notes were
built from. All four installers move faster than this page does. **Where an installer's behaviour
has changed since, its own `--help` wins over this page** — and if you find a difference, please
report it, because that is the failure mode this section exists to make visible.

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
protection, and the two APIs disagree in a way that will waste your time:
`repos/.../branches/main/protection` returns **404 "Branch not protected"** while
`repos/.../branches/main` returns **`protected: true`**. Both are accurate about their own question.
Check `repos/.../rulesets`. Both rulesets allow organisation and repository admins to bypass them,
so they guard against accident rather than against the maintainer; that matches the threat model
stated below.

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
4. Install: `pwsh scripts/install-gate.ps1 -GitHubUser <your login>`. It aborts at the first failed
   guard and writes nothing on abort. The guards, in order: the clone is not nested inside another
   work tree; the identifier list and the pinned scanner are both in place; `pwsh` and both root
   validators (`claude`, `gh`) are on PATH; **`core.hooksPath` is unset in every scope**; and a
   remote `origin` exists. Then it writes the two hooks, records the list and scanner paths in local
   git config, rewrites the `origin` URL to HTTPS **if it is a github.com URL** — a non-GitHub origin
   is left alone — and sets a credential helper that reads your token from the `gh` keyring. Safe to
   re-run.
   ⚠ **The `core.hooksPath` guard catches people out.** If you use husky, a global hooks directory,
   or anything else that sets it, this aborts and tells you nothing about which scope set it. Check
   `git config --show-origin --get core.hooksPath`.

**Maintaining requires Windows or Linux.** The scanner fetcher and the gate both resolve a pinned
binary for Windows x64 or Linux x64 and throw on anything else, so the gate cannot run on macOS, and
CI runs neither macOS nor any arm64 host. The platform test looks at the operating system only and
never at the architecture, so on an arm64 machine the fetch takes the x64 branch, downloads the x64
binary and **passes both hash checks** — the pins are on the x64 artefact. Whether it then runs is
up to your emulation layer. Untested here; treat arm64 as unsupported rather than as known-broken.

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
