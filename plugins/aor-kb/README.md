# aor-kb

Preview `0.x`. An evidence-graded knowledge base for agents: every claim records **how you know
it**, and the query tool serves a claim back only with the trust label that grade earns. Prose is
never authority — only `- [kind] … {…}` list items are claims.

| Skill | What it does |
|---|---|
| `aor-kb-query` | Searches one KB and prints every matching claim **with its trust label**, so you never act on a weak, quarantined or stale claim by accident. Read-only. |
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

## Upgrading from 0.1.6

Older versions logged each query to `kb/_serve/` and took `--no-log`. 0.1.7 does neither: a script
that passes `--no-log` now exits 2, and the `kb/_serve/` directory can be deleted.

## Not in this preview

- **No pre-commit hook.** A hook has to find the tools at commit time, and under a plugin install
  the only path it could record is a versioned cache directory that the next version bump orphans.
  Adopters therefore have no write-time boundary backstop; the primary guard is the write-time
  check inside `kb_capture` itself.
- **No POSIX support**, and no cross-platform CI.

MIT licensed — see `LICENSE`.
