# aor-kb

Preview `0.x`. An evidence-graded knowledge base for agents: every claim records **how you know
it**, and the query tool serves a claim back only with the trust label that grade earns. Prose is
never authority — only `- [kind] … {…}` list items are claims.

| Skill | What it does |
|---|---|
| `aor-kb-query` | Searches one KB and prints every matching claim **with its trust label**, which says how the claim is known. The label informs you; it does not stop you acting on a weak or stale claim. Read-only. |
| `aor-kb-capture` | Records what a session learned as a graded claim, behind a fail-closed boundary guard that binds the folder you are working in to exactly one KB. |
| `aor-kb-setup` | Wires this machine to a KB you have already cloned: dependency check, config root, manifest entry, and the repo scaffold. It never creates a repository, commits, pushes, or registers a workspace. |

## Requirements

- **Windows only.** The tools resolve their config root through Win32 known-folder APIs, and the
  junction guard reads a Windows-only stat field. Off Windows `is_reparse()` **raises** rather than
  answering `False` — a guard that cannot see reparse tags would pass every path while reporting
  success, which is worse than not having one.
- **Python 3.9 or later**, on `PATH` as `python`.
- **`strictyaml`**, importable by that interpreter. It is the only third-party
  dependency, pinned in [`requirements.txt`](requirements.txt):

  ```
  python -m pip install -r <plugin-root>/requirements.txt
  ```

  Every tool that imports it checks at import time and exits **3** naming exactly what is
  missing, rather than failing on an `ImportError` traceback. Exit 3 is distinct from
  1 (gating) and 2 (usage) so a caller can tell a missing dependency from a failing KB.

## First run

**The short path: run `/aor-kb:aor-kb-setup` from inside a clone of your KB repository.** It does
everything below — checks the dependency, creates the config root, writes this machine's manifest
entry, and scaffolds the repo if it is empty — and stops before committing anything, so you review
the diff yourself. Run it once per KB.

The long path, if you would rather do it by hand:

**1. You need a KB repository, and nothing here creates one.** Create it on GitHub and clone it. It
must be a git repo whose `origin` owner and `git config user.email` match what you put in the
manifest below — both are checked on every call, and both fail closed.

**2. Create the config root.** Neither tool works until it exists; the manifest is a fail-closed
precondition.

```
python <plugin-root>/tools/kb_capture.py init
```

`init` writes `instances.yml` from the shipped placeholder template, creates an empty workspace
registry and an empty work-path list, and prints the directory it wrote them to. It is
non-interactive and it never overwrites an existing manifest.

**3. Point an instance at your clone.** Edit `instances.yml` — `root`, `remote_owner` and `identity`
for `work`, `personal`, or both. **Those two names are the only writable ones:** any other name is
readable but refused on every write, because the boundary policy defines exactly two directions.

**4. Give the repository the three files it needs.** These live in the KB repo, not here, and
nothing in this plugin creates them:

| File | Why |
|---|---|
| `.kb-lint.yml` at the repo root | Copy `<plugin-root>/.kb-lint.yml` and **set `instance:`** to match the manifest keyword. The shipped copy leaves it commented out on purpose — the template is not itself an instance — and without it every call fails closed. |
| `kb/index.md` | The index the query tool reads. Frontmatter `okf_version: "0.1"`, then a `# kb index` heading. |
| `kb/_serve/` in `.gitignore` | Versions up to 0.1.6 logged every query under that directory. The query tool writes nothing there now; the rule keeps a clone shared with someone on an older version from tracking their log. |

**5. Confirm it works:**

```
python <plugin-root>/tools/kb_query.py --instance personal
```

Exit 0 is a pass — `served 0 of 0 claims` on a new KB means the whole destination check cleared. ⚠
**Do not verify with `route`.** `route` is a write preflight, so it also applies the workspace
policy, and it will refuse from a folder that has not yet chosen a KB — which is every folder on a
fresh install, including your clone. That refusal is the workspace binding working, not a broken
setup.

The config root lives outside this plugin directory, because a package manager owns and replaces
the plugin directory on every version bump. There is no environment variable to relocate it: one
variable would silently redirect all four boundary inputs at once, with no banner and nothing to
acknowledge, so the channel is refused rather than guarded.

## Trust tiers

Computed from each claim's verification method, never stored:

| Tier | Method | Served |
|---|---|---|
| T1 verified | `ran-tool`, `read-primary-source` | by default |
| T2 attested | `author-asserted` | by default, weaker |
| T3 quarantined | `model-inferred` | only with `--include-quarantined` |
| T4 quarantined | `unverified` | only with `--include-quarantined` |

Retirement is a separate axis: `superseded` and `deprecated` claims are hidden unless you ask for
`--history`. Never treat a quarantined, superseded or deprecated claim as current truth.

The tier names are not printed. A served claim is one line,
`[kind|basis|conf|id|flag] claim text (src: …)`, and you read the tier off it:

| Field | What it holds |
|---|---|
| `basis` | For a fact or procedure, its `v:` method and date, such as `ran-tool 2026-09-24`; the method gives the tier. For a decision, its status and date. For a lesson, `seen <n>, confirmed <date>`. Decisions and lessons are always T2. |
| `conf` | The confidence grade, or `-` where there is none. `QUARANTINED` for T3 and T4, and `QUARANTINED:<check>` for a claim with any lint finding, a fixable one included. |
| `flag` | Only on a retired or expired claim: `SUPERSEDED-BY: <id>`, `DEPRECATED: <reason>` or `EXPIRED: <date>`. |

A claim in a file past its `evidence.review` date carries no flag and is still served; `--fresh` hides it.

## Upgrading from 0.1.7

Run this once after upgrading, from the root of each knowledge-base clone. `<plugin-root>` is the 0.1.8
plugin's directory, the one whose `.claude-plugin/plugin.json` reads `0.1.8`; an older version's directory
can still sit beside it, and its tools do none of this. `kb/` is the default `kb_path`: if your
`.kb-lint.yml` sets another, use that directory in both commands.

```
git status --porcelain -- kb/
python -E -B <plugin-root>/tools/kb_lint.py fix kb/
```

The first command must print nothing, so the fix writes only into files that hold committed claims and
nothing else. Then review and commit every file the fix prints after `wrote:`. If the fix exits 1, it has
still made its other repairs: commit the files it printed, correct by hand each claim it names as gating
(the refused shapes below say how), commit those edits, and run it again. Repeat until it exits 0.

From here on, `kb-lint` and `kb-query` are short for `python -E -B <plugin-root>/tools/kb_lint.py` and
`python -E -B <plugin-root>/tools/kb_query.py`.

What changes:

- **A supersede flips the older claim's status.** It now reads `status: superseded` whatever it
  carried before (a decision's `accepted`, a lifted lesson's `promoted`, which keeps its
  `promoted-to:`). A claim that already carries a `superseded-by:` link with an older status is
  reported as fixable (`stale status`), and that one `fix` run repairs it. A `deprecated` claim is
  never flipped.
- **Some supersedes are refused**, with a gating error on the claim that carries the link, and the
  claim they point at is never written. A capture that would create one writes nothing at all; a
  `kb-lint fix` run still makes its other repairs, such as assigning a missing id. The refused shapes:
  - onto a `deprecated` claim, even when both links are already in place: write the corrected claim
    without a link and leave the tombstone. A tombstone that carries `superseded-by:` itself gates
    too; remove that link;
  - onto a claim another claim already supersedes: supersede the chain head instead. If the older
    claim has no `superseded-by:` yet, every claim superseding it gates, and no back-link is written
    until you remove all but one of those links by hand;
  - a claim whose `superseded-by:` names a claim that supersedes a different one, and two claims
    naming the same successor when that successor links back to neither.

  So a knowledge base that already holds two superseders of one claim gets exit 1 from that upgrade
  `fix`, and a capture into either topic file is refused until one link is edited by hand.
- **Links resolve across topic files.** A `supersedes:` whose target lives in another topic file is a
  normal write: the check resolves it against the whole knowledge base, and the fix writes the
  reciprocal and the status into the other file. The gate stays on the files you name or stage.
  Errors already in other files are not reported, but a fixable finding about a linked claim is printed
  under its own file's name. A fix run that introduces a gating error into a file it followed a link
  into exits 1 and names that file.
- **An id that collides with one in another file gates**, even when you check a single file.
- **A capture into a topic file with any gating error is refused**, and nothing is written; the
  REFUSED line names the claim that gates. The capture's dry run reports on that topic file only, so an
  error in another file never refuses it.
- **A capture writes exactly what it was given, or nothing.** It parses the line it would append and
  refuses at exit 2, writing nothing, unless the line reads back as exactly the given text and fields.
  Two inputs fail that test:
  - a `--text` that ends in a `{...}` block when no field is given: the line has no brace of its own,
    so that block would be read as the claim's metadata. With any field given, the text is written as
    prose;
  - a field value holding a `{` but none of `,` `}` `:`. It is written unquoted, and the `{` would
    move where the metadata starts. A value holding `,`, `}` or `:` is quoted and written as given.

  Also refused at exit 2: an empty or whitespace-only `--text`; a double quote in a brace value (`--src`,
  `--why` and the rest), since the grammar cannot hold one inside a quoted value (use single quotes); a
  control or line-break character
  in any value, U+0085, U+2028 and U+2029 included; and text that cannot be written as UTF-8, such as
  half of a surrogate pair. `--topic` must be a plain file name: no path segment may hold
  `< > : " | ? *`, end in a dot or a space, be a device name such as `nul`, or hold a `~` followed by a
  digit (the shape of a Windows 8.3 short name, which can resolve to another, longer-named file); the
  file name may not be empty or only dots and whitespace (`./`, `.md`, `.`, `...`); and it may not name a reserved file
  (`index.md`, `log.md`) in any letter case.
- **A capture refuses when another file it would change has uncommitted changes**, staged or not. Such a
  file is one the fix after the write would write a back-link into: the file holding the claim that
  `--supersedes` or `--superseded-by` names, or one that a link from a claim already in the topic file
  points into. The capture exits 1 with REFUSED, writes nothing, and names every such file; so does its
  dry run, and `reconfirm` refuses the same way. Committing that file would carry those changes too. If
  they are your own earlier captures or reconfirms, commit them first, then continue. (The topic file
  the capture appends to is not checked, nor is the generated `kb/index.md`; see the capture page's
  step 5.)
- **A capture refuses while a topic file's name cannot be written as UTF-8** (half of a surrogate pair,
  which an earlier version's `--topic` could create). The index refresh after the write would fail on it,
  so the capture writes nothing and names the file; rename it.
- **A machine-local hook's check now gates a fixable finding.** Under `kb-lint check --changed --hook`,
  a missing back-link, a missing id or a stale status that is still there at commit time refuses the
  commit, whether the hook's fix could not write it or no fix ran (see "Not in this preview").
- **A query narrowed to a path resolves outgoing links only.** `kb-query kb/<file>.md` resolves a link
  from that file into another one. It does not compute a link into it from outside, so a claim
  superseded from another file whose back-link is missing is served there as current. Without the path,
  or with `--topic`, that claim is held back instead: the missing back-link is a finding, so it shows as
  `QUARANTINED:L7` under `--include-quarantined`. Once `kb-lint fix` writes the back-link, it reads
  `SUPERSEDED-BY:` under `--history`.

## Upgrading from 0.1.6

Older versions logged each query to `kb/_serve/` and took `--no-log`. 0.1.7 does neither: a script
that passes `--no-log` now exits 2, and the `kb/_serve/` directory can be deleted.

## Not in this preview

- **No pre-commit hook ships.** A hook has to find the tools at commit time, and under a plugin
  install the only path it could record is a versioned cache directory that the next version bump
  orphans. The guard every adopter has is the check `kb_capture` runs before it appends. An instance
  MAY carry its own machine-local hook (one that reads the interpreter and the tools' directory from
  its local git config); where one runs, a refusal at commit time is the gate working, and the hook
  is never bypassed with `--no-verify`. **A hook must stop when `kb-lint fix --changed --hook` exits
  non-zero**: read that command's own exit status, never through a pipe, whose status is the last
  command's (`fix … | tee` reports the `tee`). That fix judges the commit from the
  index and writes only files whose working tree equals their staged copy. If a file it must write has
  unstaged changes, it writes nothing there, exits 1 and names the file; stage or stash that file, then
  commit again. As a backstop, `kb-lint check --changed --hook` refuses a commit that still carries any
  fixable finding. To clear it, run `kb-lint fix --changed --hook`, stage exactly the files it names, and
  commit again; a plain `kb-lint fix` has no working-tree rule and can write into a file that holds
  someone else's unstaged edits. **A hook can widen a commit.** The fix writes into
  any file a staged link points into, and a hook that stages the files the fix names stages each one
  whole. A hook that stages more than the fix names (`git add -u`, say) can also sweep another session's
  uncommitted edits into the commit. A hook that stages what the fix names must stage each path
  literally, as `git --literal-pathspecs add -- <path>`, since otherwise a topic file named `notes[1].md`
  is a glob that also stages `notes1.md`; and it must strip the carriage return that Python on Windows
  ends each captured line with. It must also refuse a commit limited to paths (`git commit -- <paths>`,
  or `-o`) whenever the fix wrote anything: git runs the hook on a temporary index, so it would commit
  the write but leave the real index without it. A hook can tell by the name in `GIT_INDEX_FILE`: git
  calls the repository's own index `index` (or `index.lock` under `-a` and `-i`), so treat any other name
  as such a commit, which also covers an index the user set.
- **No POSIX support**, and no cross-platform CI.

MIT licensed — see `LICENSE`.
