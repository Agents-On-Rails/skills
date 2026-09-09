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
turnaround. **If you have heard nothing after two weeks, open a public issue saying only that you
sent a security report and had no reply** — no details — and it will be picked up. If a report leads
to a fix, the fix ships as an ordinary release and you will be credited unless you ask not to be.

Email is the only private channel here today. GitHub's private vulnerability reporting is not
enabled on this repository, and there is no PGP key. If sending unencrypted mail is not acceptable
to you, use the public-issue nudge above to ask for another arrangement rather than posting details.

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

**Nothing here is signed.** Every commit and tag in this repository is unsigned. A pin gets you a
specific tag, and two controls make that tag hard to move by accident: repository rulesets refuse
tag deletion and non-fast-forward tag pushes, and the publish gate refuses to repoint a tag the
remote already carries. **Neither control binds an admin** — both rulesets allow organisation and
repository admins to bypass them, and the gate is a local hook. So a pin is a strong guard against
drift and no guard at all against whoever holds the account. It does not prove who produced the
bytes, and it does not guarantee they are the same bytes as yesterday if an admin chose otherwise.
Verify what you are running if that matters to you.

**The publish gate guards against accident, not against the maintainer.** Its threat model is
private material reaching a public repository through the maintainer's own tools. It runs as local
git hooks and in CI. A clone without the hooks installed, or a direct API push, does not run it, and
the repository rulesets allow organisation and repository admins to bypass them.

**An unpinned install follows this repository.** On Copilot CLI, and on `gh skill` without a pin, an
update runs whatever has been committed here since, with whatever access you have given the skill.
**`npx skills` has no pin at all** and always follows the repository. That is how those channels
work; `README.md` says so per channel and tells you how to pin on the two channels that can.

**There is no way to reach an installed consumer.** This repository has no changelog and no
notification channel. If a release is withdrawn or found bad, there is no mechanism to tell anyone
who already installed it. Watch the repository if that matters to you.

## Supported versions

Only the latest release of each plugin. While a plugin is `0.x` there are no maintenance branches
and no backports — a fix ships in the next version and older versions stay as they are.
