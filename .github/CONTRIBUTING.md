# Contributing

Thanks for looking. This repository publishes the `aor-*` agent skills, maintained by Troels Vognsen
(<agentsonrails@trvo.dk>) as a personal open-source project. Contributions are welcome; the
following is what to expect, because some of it is unusual.

## Before you spend time on a change

**Open an issue first for anything more than a fix.** Every plugin here comes out of a design record
kept outside this repository, and a change that reads as an obvious improvement may cut against a
decision already taken for a reason that is not visible in the tree. An issue costs you five minutes
and can save you an afternoon.

Small things — a typo, a broken link, a command that does not work as documented — go straight to a
pull request. No issue needed.

**These plugins are `0.x` and move.** Names, invocation strings, flags and file locations can change
in any release, with no deprecation period. A change that depends on today's shape may not survive.

## What happens to your pull request

**CI will go red, and that is by design, not your fault.** The publish-safety gate has two
detection layers. The pattern layer and the tree rules run on your PR and report normally. The
literal layer needs a private identifier list that reaches CI as an organisation secret, and GitHub
withholds organisation secrets from fork-based pull requests — so on a fork it reports "denylist not
loaded" and the `gate` job fails. Read the other layers' output; ignore that one line.

**The merge button is never used.** A maintainer reviews the diff, fetches your branch, merges it on
`main` through the local git hooks — which run the full gate, literal layer included — and pushes.
Your commits keep their authorship. This is the only path by which anything reaches `main`, and it
exists so that no content ever lands here without the full gate having run over it.

Practical consequence: a merge may take a while, because it needs the maintainer at a machine with
the gate installed. It is not stuck.

## What the gate will refuse

Your change is scanned for content, not just reviewed. It will be refused if it contains a machine
path, a home directory, an account name, or anything matching this repository's secret patterns —
including inside a code example. If you need to show a path, use a placeholder.

Tree rules also apply: only a fixed set of entries may exist at the repository root, file names that
look like session artifacts are refused, no file may exceed 1 MB, no binary files, and every
`SKILL.md` must sit at `plugins/aor-<family>/skills/<name>/SKILL.md` or `legacy/<name>/SKILL.md`
with a frontmatter `name` equal to its directory and unique across the tree.

Running the gate yourself is possible but needs a private identifier list you will not have. The
tree and pattern layers are the parts you can meaningfully self-check; `README.md` has the setup.

## Scope

- **`plugins/`** — the shipped plugins. Changes here are adopter-visible and get the most scrutiny.
  Any change to a plugin must raise that plugin's `version` in its `plugin.json`; the gate enforces
  this on push, for every plugin the push touches.
- **`scripts/`** — the publish-safety gate and its fixture suite. A change to gate behaviour needs a
  fixture case that goes red without it, including a positive control: a check that cannot fail is
  worse than no check.
- **`legacy/`** — parked first-generation material, not installable and not maintained. Please do
  not send changes here; it is scheduled to be ported, and edits will be lost.

## Platform

The skills are Windows-only. The maintainer tooling runs on Windows and Linux x64 and throws
elsewhere, so a macOS contributor can edit and read but cannot run the gate locally. CI runs the
fixture suite on Windows and Linux.

## Licence

MIT, as in `LICENSE`. There is no CLA. By opening a pull request you are offering your contribution
under that licence.
