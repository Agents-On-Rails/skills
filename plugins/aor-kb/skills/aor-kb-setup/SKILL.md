---
name: aor-kb-setup
description: Wire this machine up to a knowledge base you have already cloned - check the dependency, create the config root, write this machine's instances.yml entry, and scaffold the KB repo if it is empty. It sets up ONE instance per run, named work or personal; it never creates a repository, never commits, never pushes, and never registers a workspace for you. Run it once per KB, from inside the clone.
allowed-tools: Bash, Read, Edit, Write, AskUserQuestion
argument-hint: "<work|personal>  |  help"
license: MIT
compatibility: "Windows only. The tools resolve their config root through Win32 known-folder APIs, and the SEC-002 junction guard raises off Windows rather than silently passing, so they do not run on macOS or Linux. Needs Python 3.9 or later on PATH as python, plus the pinned strictyaml in requirements.txt."
---

# /aor-kb-setup — wire this machine to a KB you have already cloned

**What this does NOT do, stated first because it is the boundary of the design:** it never creates a
remote repository, never runs `git commit` or `git push`, never calls `gh`, and never registers a
workspace. Everything other people will see stays yours, reviewed as a diff before it lands.

**Run it from inside the KB clone.** `git clone <the KB repo URL>` first; this skill wires up the
clone you are standing in.

## Constants (preflight — verify before running)

The tools ship inside this plugin, in `tools/` at the **plugin root** — the directory two levels
above this SKILL.md (`../../` from here). Resolve that once and use the resolved path.

```
PY   = python -E -B                       (from PATH)
CAP  = <plugin-root>/tools/kb_capture.py
QRY  = <plugin-root>/tools/kb_query.py
TMPL = <plugin-root>/.kb-lint.yml
```
`-E` ignores `PYTHON*` environment variables, so a `PYTHONPATH` cannot shadow the pinned
`strictyaml`. `-B` keeps `.pyc` out of the shipped tree. Both are part of the command — do not drop
them.

## Operating discipline

- **Invocation acknowledgement:** first line = ``Running as `aor-kb-setup` → wiring the <work|personal> KB.``
- **Help:** if the argument is `help`, `--help`, `?`, or empty **and** the user has not said which KB
  this is, print the HELP block below verbatim and stop.
- **One instance per run.** A dual-context user runs this twice — once in each clone. There is no
  "set up both" mode, because each run is about the repository you are standing in.
- **The instance name is `work` or `personal`. There is no third name.** The boundary policy defines
  exactly two writable directions; a name outside them is readable but refused on every write, so
  offering one here would hand someone a KB they cannot capture into. If the user asks for a third
  name, say that plainly and stop — do not write it.
- **Never `cd` out of the clone** mid-flow. Step 7's registration command is deliberately for the
  user to run later, elsewhere.

## Flow — 8 steps

### 0. Check this plugin actually arrived intact

Before anything else, verify the siblings exist: `<plugin-root>/tools/kb_capture.py`,
`kb_boundary.py`, `kb_lint.py`, `kb_query.py`, and `<plugin-root>/requirements.txt`.

If any are missing you are almost certainly running a **skill-only** copy: some install channels
deliver a skill's own folder and nothing above it, so this file can arrive alone. **Say exactly
that** — "the skill arrived but the plugin's tools did not" — and point at the canonical
documentation:

> https://github.com/Agents-On-Rails/skills/tree/main/plugins/aor-kb

Then stop. **Do not reproduce the install instructions here**; they live in that README and nowhere
else, and a second copy would drift from it.

### 1. Check the interpreter and the dependency

```
<PY> <CAP> --help
```
Exit 3 means `strictyaml` is missing. **Surface the tool's own message verbatim** — it names exactly
what is absent — and give the install line from the plugin README (`pip install -r requirements.txt`).
Do not diagnose further; exit 3 is already precise.

### 2. Create the config root

```
<PY> <CAP> init
```
Non-interactive, and it **never overwrites an existing manifest**. Report the directory it echoes.
If it prints a `NOTE:` about entries that are readable but not writable, relay it as-is — it is
telling the user their manifest names an instance the boundary will refuse on write.

### 3. Ask ONE question: which GitHub owner holds this KB

Ask via `AskUserQuestion` (or in plain text where that is unavailable):

> *"Which GitHub owner or organisation holds this knowledge base?"*

One short string the user already knows — an owner, not a URL. **Ask nothing else.** Then derive the
other three values, from the clone you are standing in:

```
git rev-parse --show-toplevel        -> root     (the clone's own path)
git config user.email                -> identity (how this machine commits here)
git remote get-url origin            -> the ACTUAL owner
```
If `origin` is missing or the path is not a git repo, stop and say so: this skill wires an existing
clone and has nothing to wire without one.

### 4. Compare stated against derived. Unequal → HALT and write nothing

Parse the owner out of the origin URL and compare it to what the user said.

- **Equal** → continue to step 5.
- **Unequal** → **STOP. Write nothing at all** — not the manifest entry, not the scaffold. Report
  both values and say plainly that the clone does not belong to the owner they named, so this may be
  the wrong repository.

🛑 **This comparison is the whole point of asking.** If setup derived both sides it would compare the
repository to itself, agree every time, and could not fail on the day it matters. Asking for the one
boundary-defining value is what makes the check able to fire. **Never "fix" a mismatch by using the
derived owner** — that silently restores the tautology.

### 5. Write this machine's `instances.yml` entry

Edit the manifest in the config root `init` reported. Fill in the chosen instance's block only:

```
<work|personal>:
  root: "<the clone path from step 3>"
  remote_owner: <the owner THE USER STATED in step 3>
  identity: "<the git user.email from step 3>"
```

🛑 **`remote_owner` is the value the user gave you, never the one you derived from `origin`.** This is
what makes step 4 structural instead of a prompt someone can read past: the tool's own destination
check compares this field against the clone's live origin owner on **every** route and **every**
capture, so if the two ever disagree the boundary fails closed — at step 7 today, and again on any
day the remote changes under it. Writing the derived value instead would make the manifest agree with
itself forever, which is the tautology step 3 exists to break.

Leave the other instance's placeholder untouched unless the user is setting that one up too (a
second run). **Write `work` or `personal` and nothing else.** Show the user the block before writing
it.

### 6. Scaffold the clone — only what is missing. STOP before the commit

A KB repo needs three things this plugin does not create. Add only the ones absent:

- **`.kb-lint.yml`** — copy `<TMPL>` to the clone root and set `instance: <work|personal>` to match
  step 5. The shipped template leaves that key commented out on purpose, because the template itself
  is not an instance; **an instance must set it, or every call fails closed.**
- **`kb/index.md`** — the index the query tool reads:
  ```
  ---
  okf_version: "0.1"
  ---
  # kb index
  ```
- **`.gitignore`** — append `kb/_serve/` if not already ignored. The query tool appends a serve-log
  under that directory on every read, so without the rule each person's queries produce a growing
  tracked file that conflicts on every pull.

**Someone joining an existing KB will find all three present — that is the expected outcome, not a
failure.** Say so rather than reporting "nothing to do" as if something went wrong.

🛑 **Stop here. Do not `git add`, `git commit` or `git push`.** Show the user what changed and hand
the review to them. An increment that adds "and commit it for you" is reversing this rule, not
extending it.

### 7. Verify, then explain registration and hand over the command

Verify with the **read** path:

```
<PY> <QRY> --instance <work|personal>
```
Expect exit 0 — `served 0 of 0 claims` on a brand-new KB is a **pass**, not an empty result. This one
command exercises everything setup just wired: the manifest entry, that the root is a git repo, that
its origin owner matches what the user stated, that `user.email` matches, and that `.kb-lint.yml`
declares the right instance. If it HALTs, translate the message plainly — it names which of those
legs failed, and that is the boundary doing its job.

🛑 **Do NOT verify with `route`.** `route` is a write preflight, so it also applies the workspace
policy — and the clone is not a registered workspace, because step 6 of this skill deliberately does
not register one. `route` therefore HALTs here with *"not assigned to a KB"* **even when setup
succeeded perfectly**. Measured, not assumed. Ending setup on that refusal would teach a new user
that their brand-new KB is broken. If someone reports it, the answer is that they ran the write
preflight from a folder that has not chosen a KB yet — which is step 7's next paragraph.

Then tell the user the one thing that is **not** done, before they meet it as a refusal:

> Captures are bound to the folder you run them from, not to this KB. The first capture from each
> project folder asks once which KB that folder feeds. You can answer it then, or register a folder
> ahead of time by running this **from that folder**:
>
> ```
> <PY> <CAP> register-workspace --choice <work|personal> --operator-confirm
> ```

**Do not run it for them, and do not run it here.** This clone is not a folder anyone captures
*from*; registering it would register the wrong folder, and consent about a folder belongs with the
person who knows what that folder holds.

## HELP

```
aor-kb-setup — wire this machine to a knowledge base you have already cloned.

WHAT IT DOES  Checks the dependency, creates the config root, writes this machine's instances.yml
              entry for ONE instance, and scaffolds the KB repo if it is missing .kb-lint.yml,
              kb/index.md, or a .gitignore rule for the serve-log. Then it verifies the boundary
              check clears and tells you how to register a project folder.
WHAT IT NEVER Creates a repository. Commits. Pushes. Calls gh. Registers a workspace for you.
DOES          Writes any instance name other than work or personal.
BEFORE YOU    git clone <the KB repo URL>, then run this from inside that clone.
RUN IT
USAGE         aor-kb-setup work        (the shared/work KB)
              aor-kb-setup personal    (your own KB)
              Run it once per KB. Two contexts = two clones = two runs.
THE ONE       Which GitHub owner holds this KB. Everything else is derived from the clone -- and
QUESTION      the answer is compared against what the clone actually points at, so naming the
              wrong repository stops setup instead of quietly configuring it.
AFTER IT      Review the scaffold diff and commit it yourself. Then capture from a project folder;
RUNS          the first capture there asks once which KB that folder feeds.
```

**Copilot CLI (no button UI):** run the same tools; where `AskUserQuestion` is unavailable, ask the
owner question in plain text and wait for the answer before step 4. The HALT in step 4 is identical
across tools — neither can write a manifest entry without clearing it.
