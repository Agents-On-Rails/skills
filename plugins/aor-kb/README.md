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
  missing, rather than failing on an `ImportError` traceback. Exit 3 is distinct from 1
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

Run `kb-lint fix kb/` once after upgrading. What changes:

- **A supersede flips the older claim's status.** It now reads `status: superseded` whatever it
  carried before (a decision's `accepted`, a lifted lesson's `promoted`, which keeps its
  `promoted-to:`). A claim that already carries a `superseded-by:` link with an older status is
  reported as fixable (`stale status`), and that one `fix` run repairs it. A `deprecated` claim is
  never flipped.
- **Some supersedes are refused before anything is written**, with a gating error on the claim that
  carries the link:
  - onto a `deprecated` claim, even when both links are already in place: write the corrected claim
    without a link and leave the tombstone. A tombstone that carries `superseded-by:` itself gates
    too; remove that link;
  - onto a claim another claim already supersedes: supersede the chain head instead. If the older
    claim has no `superseded-by:` yet, every claim superseding it gates, and nothing is written until
    you remove all but one of those links by hand;
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
- **A capture refuses a double quote in a brace value** (`--src`, `--why` and the rest) at exit 2: the
  grammar cannot hold one inside a quoted value. Use single quotes instead.
- **A query narrowed to a path resolves outgoing links only.** `kb-query kb/<file>.md` resolves a link
  from that file into another one. It does not compute a link into it from outside, so a claim
  superseded from another file whose back-link is missing is served there as current. Query without the
  path, or with `--topic`, to see it retired.

## Upgrading from 0.1.6

Older versions logged each query to `kb/_serve/` and took `--no-log`. 0.1.7 does neither: a script
that passes `--no-log` now exits 2, and the `kb/_serve/` directory can be deleted.

## Not in this preview

- **No pre-commit hook ships.** A hook has to find the tools at commit time, and under a plugin
  install the only path it could record is a versioned cache directory that the next version bump
  orphans. The guard every adopter has is the check `kb_capture` runs before it appends. An instance
  MAY carry its own machine-local hook (one that reads the interpreter and the tools' directory from
  its local git config); where one runs, a refusal at commit time is the gate working, and the hook
  is never bypassed with `--no-verify`. **A hook can widen a commit.** The fix writes into any file a
  staged link points into, and a hook that stages the files the fix names stages each one whole.
  `kb-lint fix --changed --hook` judges the commit from the index and writes only files whose working
  tree equals their staged copy. If a file it must write has unstaged changes, it writes nothing there
  and fails, naming the file; stage or stash that file, then commit again. A hook that stages more than
  the fix names (`git add -u`, say) can also sweep another session's uncommitted edits into the commit.
- **No POSIX support**, and no cross-platform CI.

MIT licensed — see `LICENSE`.
