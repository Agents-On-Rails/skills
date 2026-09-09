# Security policy

This repository publishes the `aor-*` agent skills. It is maintained by Troels Vognsen
(<agentsonrails@trvo.dk>) as a personal open-source project. `Agents-On-Rails` is a GitHub
organisation used for that work; there is no company behind it, no support contract and no
response-time commitment.

## Reporting a problem

Email <agentsonrails@trvo.dk>. Please do not open a public issue for something you believe is
exploitable.

Include what you did, what happened, and which version you were running — the `version` in the
plugin's `plugin.json`, or the tag or commit you installed from. A minimal reproduction is worth
more than a description.

You will get an acknowledgement when the maintainer next picks up mail. There is no guaranteed
turnaround. If a report leads to a fix, the fix ships as an ordinary release and you will be
credited unless you ask not to be.

## Scope

**In scope**

- The two shipped plugins under `plugins/`, and the Python they run on your machine.
- The publish-safety gate under `scripts/`, including a way to get content past it that it should
  have refused.
- The install and update instructions in `README.md`, if following them does something other than
  what they say.

**Out of scope**

- `legacy/` — parked first-generation material, not installable through any channel and not
  maintained.
- The behaviour of Claude Code, GitHub Copilot CLI, `gh skill` or `npx skills` themselves. Report
  those to their own maintainers.
- The absence of code signing, and anything that follows from it. See below.

## What this project does not protect against

These are known and accepted, not oversights. Knowing them may save you a report.

**Nothing here is signed.** Every commit and tag in this repository is unsigned. A pinned tag is
immutable — repository rulesets refuse tag deletion and non-fast-forward tag pushes, and the publish
gate refuses to repoint a tag the remote already carries — so a pin gets you the same bytes every
time. It does not prove who produced them. Anyone who obtained push access to the account could
publish as the maintainer.

**The publish gate guards against accident, not against the maintainer.** Its threat model is
private material reaching a public repository through the maintainer's own tools. It runs as local
git hooks and in CI. A clone without the hooks installed, or a direct API push, does not run it, and
the repository rulesets allow organisation and repository admins to bypass them.

**An unpinned install follows this repository.** On Copilot CLI, and on `gh skill` or `npx skills`
without a pin, an update runs whatever has been committed here since, with whatever access you have
given the skill. That is how those channels work; `README.md` says so per channel and tells you how
to pin where pinning is available.

**There is no way to reach an installed consumer.** This repository has no changelog and no
notification channel. If a release is withdrawn or found bad, there is no mechanism to tell anyone
who already installed it. Watch the repository if that matters to you.

## Supported versions

Only the latest release of each plugin. While a plugin is `0.x` there are no maintenance branches
and no backports — a fix ships in the next version and older versions stay as they are.
