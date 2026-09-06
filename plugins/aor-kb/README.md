# aor-kb

Preview `0.x`. An evidence-graded knowledge base for agents: every claim records **how you know
it**, and the query tool serves a claim back only with the trust label that grade earns. Prose is
never authority — only `- [kind] … {…}` list items are claims.

| Skill | What it does |
|---|---|
| `aor-kb-query` | Searches one KB and prints every matching claim **with its trust label**, so you never act on a weak, quarantined or stale claim by accident. Read-only. |
| `aor-kb-capture` | Records what a session learned as a graded claim, behind a fail-closed boundary guard that binds the folder you are working in to exactly one KB. |

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

Neither tool works until a config root exists — the manifest is a fail-closed precondition. Create
one:

```
python <plugin-root>/tools/kb_capture.py init
```

`init` writes `instances.yml` from the shipped placeholder template, creates an empty workspace
registry and an empty work-path list, and prints the directory it wrote them to. It is
non-interactive and it never overwrites an existing manifest. Then point each instance in
`instances.yml` at your own KB repository, and confirm the boundary check clears:

```
python <plugin-root>/tools/kb_capture.py route --instance personal
```

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

## Not in this preview

- **No pre-commit hook.** A hook has to find the tools at commit time, and under a plugin install
  the only path it could record is a versioned cache directory that the next version bump orphans.
  Adopters therefore have no write-time boundary backstop; the primary guard is the write-time
  check inside `kb_capture` itself.
- **No POSIX support**, and no cross-platform CI.

MIT licensed — see `LICENSE`.
