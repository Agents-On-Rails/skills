#!/usr/bin/env python3
"""kb-boundary -- the shared employer-boundary core (SEC-002, pilot P4).

Extracted from kb_capture so the read-only consumer (kb_query) and the write
path (kb_capture add) import ONE boundary abstraction instead of a sibling
*command* module. Holds two independent structural controls:

  1. DESTINATION check (d-route, spec §14.2) -- resolve_instance(): keyword ->
     instances.yml manifest -> the TARGET repo's live git remote owner +
     user.email + its .kb-lint.yml instance:. Answers "is the repo I'm about to
     write genuinely that instance?". Never inspects the caller.

  2. WORKSPACE-choice binding (SEC-002, spec §14.7) -- enforce_workspace_policy():
     ties the CALLING workspace (resolved Path.cwd()) to a required instance and
     REFUSES a mismatched write. Asymmetric by design: writes to the WORK KB flow
     (low harm); writes to the PERSONAL KB (irreversible: work content -> personal
     GitHub) are guarded by a live, re-derived work-signal veto plus a per-folder
     operator choice + a per-save human confirm. Machine-local registry
     (workspaces.jsonl, never pushed). Normative: `okf-e-profile.md` §14.7.

resolve_instance()          bare destination check (READ path: kb_query/SEC-003)
resolve_instance_guarded()  workspace policy + destination check (WRITE + route
                            preflight: any subcommand whose success greenlights a
                            write MUST call this, never bare resolve_instance)
"""

import json
import os
import re
import sys
from pathlib import Path

try:
    from strictyaml import dirty_load
except ImportError as _exc:  # aor-kb dependency guard -- K3
    # ImportError, not only ModuleNotFoundError: a present-but-broken strictyaml -- a
    # truncated install, a renamed symbol, a module shadowing it on sys.path -- raises the
    # plain parent class, and that traceback is exactly what this guard exists to replace.
    # Line 1 always names the DECLARED dependency, the one requirements.txt actually pins.
    # _exc.name can be a transitive module instead (a missing python-dateutil reports
    # "dateutil"), so it is reported separately rather than as the thing to install.
    # The literal text is ASCII, which is all these tools' stderr is guaranteed to carry at
    # import time; the interpolated paths are whatever the filesystem holds.
    _req = Path(__file__).resolve().parent.parent / "requirements.txt"
    _also = "" if _exc.name in (None, "strictyaml") else "  missing module: %s\n" % _exc.name
    sys.stderr.write(
        "aor-kb: cannot import required dependency 'strictyaml' (%s)\n"
        "%s"
        "  interpreter:  %s\n"
        "  declared in:  %s\n"
        "  install with: \"%s\" -m pip install -r \"%s\"\n"
        % (type(_exc).__name__, _also, sys.executable, _req, sys.executable, _req)
    )
    raise SystemExit(3)

# Self-locate BEFORE importing siblings (K4): under a plugin install the tools
# are reached by absolute path from a SKILL.md, so cwd is the caller's, not tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_lint  # noqa: E402
from kb_lint import die  # noqa: E402

import fnmatch  # noqa: E402

# ------------------------------------------------------------------ config root
# K2 moves operator and boundary state OUT of the code payload, and it has to move: a
# package manager OWNS and REPLACES the code directory, so an ordinary version bump would
# delete work-paths.txt and workspaces.jsonl -- the silent loss of a boundary control, with
# every message afterwards still reading normal.
#
# The root is resolved through the Windows known-folder API, and deliberately NOT through an
# AOR_KB_HOME environment variable nor a %LOCALAPPDATA% expansion. Ruled by the operator
# 2026-09-05 after an earlier layout ruling was found still-binding, never
# cited by any later record, and correct. Both naive branches were confirmed by execution:
#   * OUTSIDE the OVERRIDE_ENV guard below, one env var silently redirects all four inputs --
#     no banner, no acknowledgement -- in the direction this module calls irreversible, and
#     the harvest header would attest `active_overrides: []` while it was true of nothing;
#   * INSIDE that guard, every legitimate personal capture halts unless KB_OVERRIDE_ACK is
#     set permanently, which fails-open the three overrides the guard exists for.
# There is no third setting of the guard list, so the env channel itself is refused -- the
# same refusal, for the same reason, that DEFAULT_SALT has always made. %LOCALAPPDATA% and ~
# are themselves environment-derived and would reopen the channel by the back door; the API
# below does not. Test injection is untouched: the per-file KB_* seam still takes precedence
# over this root, and that is what makes an env-overridable root unnecessary.
_FOLDERID_LOCAL_APPDATA = "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"


def _known_folder(guid_str):
    """A Windows known folder read from the shell API rather than from the environment."""
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                    ("d3", wintypes.WORD), ("d4", ctypes.c_byte * 8)]

    guid = _GUID()
    if ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid_str), ctypes.byref(guid)):
        raise OSError("config root: CLSIDFromString failed")
    out = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None,
                                                    ctypes.byref(out))
    if hr != 0:
        raise OSError(f"config root: SHGetKnownFolderPath failed (hr={hr})")
    try:
        return Path(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def _token_profile_dir():
    """This user's profile directory, read from the PROCESS TOKEN. Unlike every other way of
    asking, this one cannot be moved by the environment -- measured, not assumed."""
    import ctypes
    from ctypes import wintypes

    k32, adv, uenv = ctypes.windll.kernel32, ctypes.windll.advapi32, ctypes.windll.userenv
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                     ctypes.POINTER(wintypes.HANDLE)]
    adv.OpenProcessToken.restype = wintypes.BOOL
    uenv.GetUserProfileDirectoryW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                              ctypes.POINTER(wintypes.DWORD)]
    uenv.GetUserProfileDirectoryW.restype = wintypes.BOOL

    handle = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(handle)):
        raise OSError("config root: OpenProcessToken failed")
    try:
        size = wintypes.DWORD(0)
        uenv.GetUserProfileDirectoryW(handle, None, ctypes.byref(size))
        buf = ctypes.create_unicode_buffer(size.value)
        if not uenv.GetUserProfileDirectoryW(handle, buf, ctypes.byref(size)):
            raise OSError("config root: GetUserProfileDirectoryW failed")
        return Path(buf.value)
    finally:
        k32.CloseHandle(handle)


def config_root():
    """The machine-local directory holding the four state files. Windows-only BY DECLARATION
    (P6): resolving it on POSIX would have to read the environment, which is the one channel
    this design refuses, so the tool must not run where its control does not hold.

    Two API calls, because one is not enough and that was established by probe rather than by
    reading docs. SHGetKnownFolderPath is immune to %LOCALAPPDATA% -- which is why it is used
    instead of expanding that variable -- but the value it reads is a REG_EXPAND_SZ built on
    %USERPROFILE%, so a poisoned USERPROFILE pointing at a directory that EXISTS relocates the
    whole config root silently. GetUserProfileDirectoryW reads the process token instead and
    cannot be moved at all, so it is used to fence the first result. Legitimate LocalAppData
    redirection inside the profile still works; anything outside it FAILS CLOSED rather than
    being trusted, because at that point the environment has chosen the boundary's inputs."""
    if sys.platform != "win32":
        raise OSError("aor-kb is Windows-only: the config root is resolved through Windows "
                      "APIs that read the process token, and the environment-derived "
                      "alternative is refused by design (see the config-root note above)")
    from_token = _token_profile_dir() / "AppData" / "Local"
    from_shell = _known_folder(_FOLDERID_LOCAL_APPDATA)
    if from_token.resolve() != from_shell.resolve():
        raise OSError("config root: the shell and the process token disagree about "
                      "LocalAppData -- refusing, because one of the two has been moved by "
                      "the environment and this code cannot tell which")
    return from_token / "aor-kb"


CONFIG_ROOT = config_root()

DEFAULT_MANIFEST = CONFIG_ROOT / "instances.yml"
DEFAULT_REGISTRY = CONFIG_ROOT / "workspaces.jsonl"
DEFAULT_WORKPATHS = CONFIG_ROOT / "work-paths.txt"
# B3/M10: the keyed-digest salt lives BESIDE the two registries (machine-local, gitignored
# in the same commit that created this constant, on the probe-harness *.salt copy-exclusion
# denylist). NO env override exists on purpose: an env channel would be an attacker-chosen
# key (keying silently defeated); tests inject a path parameter instead.
# It moves WITH the root (operator, 2026-09-05): load_digest_salt below mints a fresh key on a
# merely-MISSING file, so leaving the salt behind in the replaceable code directory would
# rotate the production key on the first version bump and orphan every emitted digest.
DEFAULT_SALT = CONFIG_ROOT / "digest.salt"


class SaltError(RuntimeError):
    """Any digest-salt failure. Fail-closed: the caller must refuse, never fall back."""


def load_digest_salt(path=None):
    """The keyed-digest salt: exactly 32 bytes, created with os.urandom on first use.
    The read and create arms are DISTINCT fail-closed paths (B-lens F9/F11/F12): only a
    MISSING file may create (a read error must never silently rotate the production key,
    orphaning every previously emitted digest), creation is O_EXCL (a lost race re-reads
    the winner's salt), and any wrong length HALTs (an empty file would key HMAC with
    b"" -- keying silently defeated while every digest still looks valid)."""
    p = Path(path or DEFAULT_SALT)
    try:
        salt = p.read_bytes()
    except FileNotFoundError:
        try:
            with open(p, "xb") as f:
                f.write(os.urandom(32))
        except FileExistsError:
            pass                            # lost the creation race -- the winner's salt stands
        except OSError as exc:
            raise SaltError(f"digest salt uncreatable ({type(exc).__name__})")
        salt = p.read_bytes()
    except OSError as exc:
        raise SaltError(f"digest salt unreadable ({type(exc).__name__}) -- NOT regenerating")
    if len(salt) != 32:
        raise SaltError(f"digest salt is {len(salt)} bytes, expected 32 -- refusing a weak key")
    return salt
# R14: the boundary-signal values (employer domains, work owner suffixes, advisory name
# patterns) are OPERATOR-OWNED config in the machine-local manifest's optional `boundary:`
# section -- never literals in shipped code.
# Empty/absent config = those legs stay silent; the work-path veto and the workspace
# registry never depend on them.
_BOUNDARY_KEYS = ("work_domains", "work_owner_suffixes", "advisory_name_patterns")
# host + first path segment (owner) from a git remote, HTTPS or SSH. `(?::\d+)?` drops an
# explicit HTTPS port so `https://github.com:443/PersonalOwner/x` still yields owner=PersonalOwner (a bare
# digit port would otherwise be misread as the owner); an SSH `git@host:owner` keeps `:owner`
# because the port group only matches digits.
REMOTE_RE = re.compile(
    r"^(?:ssh://)?(?:https?://)?(?:[^@/]+@)?(?P<host>[^:/]+)(?::\d+)?[:/]+(?P<owner>[^/]+)")
CHOICES = ("work", "personal", "both")
# Reparse tags that actually escape the boundary root on resolve(). Narrowed from "any
# reparse tag" so a cloud-sync placeholder / AppExecLink dir (which resolve()s to
# itself) is NOT dead-ended at registration -- only real junctions/symlinks are refused.
_ESCAPING_REPARSE = {0xA0000003, 0xA000000C}  # IO_REPARSE_TAG_MOUNT_POINT, _SYMLINK


# The KB_* env vars that REDIRECT the boundary's own inputs (manifest / workspace registry /
# work-path list). In PRODUCTION none are set -- the defaults point at the committed manifest +
# machine-local registry. They are the test/fixture seam. A set override neuters part of the
# boundary, so a personal (irreversible-direction) write under one must be EXPLICITLY acknowledged
# (KB_OVERRIDE_ACK), never silently trusted -- otherwise a stray override in a real shell could let
# work content route to personal GitHub (plan §3 Track 1 "KB_* override guard").
OVERRIDE_ENV = ("KB_MANIFEST", "KB_WORKSPACES", "KB_WORKPATHS")


def active_overrides():
    """The KB_* boundary-override env vars currently set (non-empty)."""
    return [k for k in OVERRIDE_ENV if os.environ.get(k)]


def _override_ack():
    return bool(os.environ.get("KB_OVERRIDE_ACK"))


def warn_overrides(active):
    """LOUD stderr banner: a guarded call is running with redirected boundary inputs."""
    print("kb-boundary: OVERRIDE ACTIVE -- " + ", ".join(active)
          + " redirect the boundary manifest/registry/work-paths (test/fixture mode); "
            "a PRODUCTION capture MUST NOT set these", file=sys.stderr)


def manifest_path():
    # KB_MANIFEST overrides the tools-repo manifest (tests point at temp fixtures).
    return Path(os.environ.get("KB_MANIFEST") or DEFAULT_MANIFEST)


def registry_path():
    # KB_WORKSPACES overrides the machine-local registry (tests + relocation).
    return Path(os.environ.get("KB_WORKSPACES") or DEFAULT_REGISTRY)


def workpaths_path():
    # KB_WORKPATHS overrides the machine-local explicit work-path list.
    return Path(os.environ.get("KB_WORKPATHS") or DEFAULT_WORKPATHS)


# ---------------------------------------------------------------- manifest + git

def load_manifest():
    mpath = manifest_path()
    if not mpath.is_file():
        die(f"boundary manifest not found at {mpath} -- routing cannot be validated "
            "(fail-closed); create instances.yml (spec §14.3)")
    try:
        data = dirty_load(mpath.read_text(encoding="utf-8"),
                          allow_flow_style=True).data
    except Exception as e:
        die(f"boundary manifest {mpath} failed to parse ({type(e).__name__})")
    data.pop("boundary", None)   # R14: the optional signal-config section, NOT an instance
    for inst, entry in data.items():
        for k in ("root", "remote_owner", "identity"):
            if k not in entry:
                die(f"manifest instance '{inst}' missing '{k}' (spec §14.3)")
    return data


def _boundary_tuple(section, key, mpath):
    """One boundary value list -> tuple of non-empty strings. A present-but-garbled value
    DIES (fail-closed: an operator typo must not silently disable the employer veto)."""
    val = section.get(key)
    if val is None:
        return ()
    if isinstance(val, str):
        val = [val]
    if not isinstance(val, list) or not all(isinstance(v, str) and v.strip() for v in val):
        die(f"boundary manifest {mpath}: boundary.{key} must be a list of non-empty "
            f"strings (fail-closed on a garbled boundary section)")
    return tuple(v.strip() for v in val)


def load_boundary():
    """The operator-owned signal config: the OPTIONAL `boundary:` section of the manifest
    (R14). Fail-SOFT on a MISSING manifest or section (empty config -- the adopter default:
    domain/owner legs silent, work-path veto + registry unaffected); fail-CLOSED (die) on a
    section that is present but unintelligible. Respects KB_MANIFEST like every other
    boundary input."""
    empty = {k: () for k in _BOUNDARY_KEYS}
    mpath = manifest_path()
    if not mpath.is_file():
        return empty
    try:
        data = dirty_load(mpath.read_text(encoding="utf-8"),
                          allow_flow_style=True).data
    except Exception:
        return empty     # routing die()s on this manifest anyway (load_manifest); signals
                         # cannot open a path routing would not already have killed
    section = data.get("boundary")
    if section is None:
        return empty
    if not isinstance(section, dict):
        die(f"boundary manifest {mpath}: `boundary:` must be a mapping "
            f"(fail-closed on a garbled boundary section)")
    return {k: _boundary_tuple(section, k, mpath) for k in _BOUNDARY_KEYS}


def _personal_entry():
    """The manifest's own `personal` instance entry (identity + remote_owner), fail-soft:
    personal_signal needs no boundary section -- the manifest already declares who
    'personal' is. Missing/unparseable manifest -> None (no positive personal signal)."""
    mpath = manifest_path()
    if not mpath.is_file():
        return None
    try:
        data = dirty_load(mpath.read_text(encoding="utf-8"),
                          allow_flow_style=True).data
    except Exception:
        return None
    entry = data.get("personal")
    if not isinstance(entry, dict) or "identity" not in entry or "remote_owner" not in entry:
        return None
    return entry


def remote_owner(root: Path):
    # A6: parse with the ANCHORED REMOTE_RE and pin the host. The unanchored
    # OWNER_RE this replaced (now removed) searched for "github.com[:/]<owner>/"
    # ANYWHERE in the URL, so it never established that github.com was the HOST: a
    # mirror-shaped origin (https://elsewhere.example/github.com/<owner>/x.git) or a
    # lookalike host (https://notgithub.com/<owner>/x.git) yielded the expected owner,
    # all four legs of _assert_repo_matches passed, and the write would land in a clone
    # that pushes elsewhere. Proven by execution in the development test suite:
    # both returned "boundary OK" before this change. REMOTE_RE is start-anchored,
    # consumes userinfo and an explicit port, and is already spoof-tested for the signal
    # path (17b) -- reusing it also removes the two parsers' disagreement and the false
    # refusal of a real github origin carrying a port (OWN-3).
    # #50: a non-zero code here is a NORMAL state (a repo with no `origin`), not an
    # outage, so it is not reported -- it yields owner=None and the sole caller,
    # _assert_repo_matches, already fails CLOSED on that. Unpacked explicitly rather
    # than discarded so the next reader can see that the code was considered.
    rc, out = kb_lint.run_git(root, "remote", "get-url", "origin")
    url = out.strip()
    if rc != 0:
        return None, url
    m = REMOTE_RE.match(url)
    ok = m is not None and m.group("host").lower() == "github.com"
    return (m.group("owner") if ok else None), url


def declared_instance(root: Path):
    """Read the repo's own .kb-lint.yml `instance:` (the self-declared identity)."""
    cfg_path = root / ".kb-lint.yml"
    if not cfg_path.is_file():
        return None
    try:
        data = dirty_load(cfg_path.read_text(encoding="utf-8"),
                          allow_flow_style=True).data
    except Exception:
        return None
    return data.get("instance")


def _assert_repo_matches(inst, entry, root: Path, blocking):
    """The three-way destination assertion. `blocking` picks the halt behaviour:
    die(exit 2) for capture routing, exit(1) for the pre-commit backstop."""
    def fail(msg):
        if blocking == "hook":
            print(f"kb-capture verify-boundary: BLOCKED -- {msg}", file=sys.stderr)
            sys.exit(1)
        die(f"boundary HALT -- {msg}")

    if not (root / ".git").exists():
        fail(f"{root} is not a git repo")
    owner, url = remote_owner(root)
    if owner is None:
        fail(f"{root} has no parseable github origin remote ({url or 'none'})")
    if owner != entry["remote_owner"]:
        fail(f"instance '{inst}' expects remote owner '{entry['remote_owner']}' but "
             f"{root} pushes to '{owner}' -- routing to the wrong repo would cross the "
             "employer boundary")
    # #50: non-zero means the identity is UNSET, which is a normal repo state and not an
    # outage. Either way this leg fails CLOSED below -- an empty email never equals the
    # manifest identity -- so the code needs unpacking, not a new branch.
    _rc_email, _out_email = kb_lint.run_git(root, "config", "user.email")
    email = _out_email.strip()
    if email != entry["identity"]:
        fail(f"instance '{inst}' expects identity '{entry['identity']}' but {root} "
             f"commits as '{email or 'unset'}'")
    decl = declared_instance(root)
    if decl is None:
        fail(f"{root}/.kb-lint.yml has no instance: key -- required, fail-closed "
             f"(expected instance '{inst}')")
    elif decl != inst:
        fail(f"{root}/.kb-lint.yml declares instance '{decl}' but the remote/identity "
             f"resolve to '{inst}' -- config drift, not trusted (fail-closed)")


def resolve_instance(keyword, manifest):
    """Keyword -> validated, boundary-checked DESTINATION root. HALTS (exit 2) on
    any mismatch. This is the bare READ path (kb_query/SEC-003); any code whose
    success greenlights a WRITE must call resolve_instance_guarded() instead."""
    if keyword not in manifest:
        die(f"unknown --instance '{keyword}' -- manifest instances: "
            f"{sorted(manifest)} (no default is permitted; §14.2.1)")
    entry = manifest[keyword]
    root = Path(entry["root"]).resolve()
    if not root.is_dir():
        die(f"instance '{keyword}' root {root} does not exist")
    _assert_repo_matches(keyword, entry, root, blocking="route")
    return root, entry


# ---------------------------------------------------------------- SEC-002 signals

def _has_git_entry(cwd: Path):
    """Is a repository INTENDED here? The discriminator for the two rc=128 cases below.

    Two channels, because git itself accepts two. (1) A .git entry at cwd or any
    ancestor -- a file or a directory both count, since a worktree's and a submodule's
    .git is a FILE and a half-created repo's is an empty directory; either way the
    repository is meant to be there. (2) GIT_DIR / GIT_WORK_TREE in the environment,
    which override discovery entirely: with those set and the target destroyed, git
    fails while NO .git exists at or above cwd, and channel (1) alone called that "not
    a repository" -- a real work tree with git down, reported as fine.

    ⚠ Note the shape of that miss: it is the same ENVIRONMENT channel item 04 closed
    for PYTHON*. `-E` does not touch GIT_*, and git still inherits the full
    environment, so the environment can take this signal down without touching disk.
    """
    if os.environ.get("GIT_DIR") or os.environ.get("GIT_WORK_TREE"):
        return True
    try:
        here = cwd.resolve()
    except OSError:
        here = cwd
    for p in [here, *here.parents]:
        try:
            if (p / ".git").exists():
                return True
        except OSError:
            continue
    return False


def git_signal_status(cwd: Path):
    """(in_git, unavailable) -- #50.

    `git rev-parse --is-inside-work-tree` exits 128 with EMPTY stdout for BOTH a
    directory that is not a repository and a repository git cannot read (a .git
    pointing nowhere, an empty .git/, a refused ownership check). Measured, with a
    real repo as the positive control. While run_git discarded the exit code those two
    were literally the same value, so every git outage read as "not a repo": work_signal
    returned None and gate 1's structural veto stopped contributing -- silently, in the
    personal and irreversible direction.

    `unavailable` separates them on the only evidence that differs: whether a .git
    entry exists at or above cwd. No .git anywhere is absent-BY-NATURE and not an
    outage; a .git that exists while git still refuses the question is an outage.
    """
    rc, out = kb_lint.run_git(cwd, "rev-parse", "--is-inside-work-tree")
    if rc == 0:
        return out.strip() == "true", False
    return False, _has_git_entry(cwd)


def report_git_unavailable(cwd: Path):
    """Tell the operator that gate 1 did not run. REPORTING, never halting (#50).

    Halting on a non-zero git code is textbook-correct and wrong here: gate 2 --
    registered choice plus a per-save human confirm -- already runs and is the real
    control, so a halt would buy little and block real work on any transient git
    failure, and friction on a safety control breeds workarounds. The defect was that
    nobody was told a signal had gone missing. This is that telling.
    """
    print(f"kb-boundary: WARNING -- git could not read the repository at {cwd}, so the "
          "structural work-signal check (gate 1) did NOT run. This is not the same as "
          "'no work signal found'. Gate 2 -- your registered choice plus your "
          "confirmation -- is the only control in force; confirm deliberately.",
          file=sys.stderr)


def _in_git(cwd: Path):
    return git_signal_status(cwd)[0]


def _email(cwd: Path):
    # #50: non-zero means user.email is unset -- a normal state, not an outage. Reached
    # only after _in_git() is True, so git itself is known to be working here.
    rc, out = kb_lint.run_git(cwd, "config", "user.email")
    return out.strip().lower() if rc == 0 else ""


def _origin(cwd: Path):
    # #50: non-zero means there is no `origin` remote -- a normal state, not an outage.
    rc, out = kb_lint.run_git(cwd, "remote", "get-url", "origin")
    if rc != 0:
        return None, None
    m = REMOTE_RE.match(out.strip())
    if not m:
        return None, None
    return m.group("host").lower(), m.group("owner")


def load_workpaths():
    """The machine-local explicit WORK-path list (git-ignored): resolved dirs the operator
    has declared work, so `work_signal` fires even on GITLESS dirs -- a work folder that is
    not a git repo has no remote or commit email to signal with. One path per line; blank / '#'-leading lines ignored. Each line is a
    LEXICAL veto target (containment via is_relative_to) -- retained regardless of whether the
    dir currently resolves/exists, so a veto NEVER silently evaporates on a transient is_dir()
    failure (cloud-sync placeholder / lock / permission blip; SEC-D1). A stale entry is inert
    (matches no cwd) and only ever OVER-restricts (the safe direction)."""
    wp = workpaths_path()
    out = []
    if wp.is_file():
        for ln in wp.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            out.append(Path(ln).resolve())
    return out


def work_path_match(cwd: Path):
    """The registered work-path containing cwd (containment, reusing the SEC-001 idiom)."""
    cwd = cwd.resolve()
    for p in load_workpaths():
        if cwd == p or cwd.is_relative_to(p):
            return p
    return None


def advisory_work_hint(cwd: Path):
    """ADVISORY ONLY (never enforcement): does the folder NAME match an operator-listed
    work-ish pattern (boundary.advisory_name_patterns)? Surfaced at the forced choice to
    nudge the operator; it decides nothing (a work-ish name also lives in personal context,
    so a pattern can only warn)."""
    name = cwd.name.lower()
    for pat in load_boundary()["advisory_name_patterns"]:
        if fnmatch.fnmatch(name, pat.lower()):
            return pat
    return None


def work_signal(cwd: Path):
    """Reason string if the cwd shows a WORK signal, else None (§4). The explicit work-path
    list is checked FIRST so it fires even on GITLESS dirs; then the live git signals (a
    gitless non-listed dir gives no signal -- git config would bleed global identity, so
    fail-open to 'no signal' and let the choice+confirm layer handle it).

    #50: that fail-open is deliberate FOR A GITLESS DIRECTORY and this docstring used to
    be the whole justification -- but the code could not tell that case apart from a git
    OUTAGE, so the same sentence silently covered a case it had never argued for. It can
    now: git_signal_status() separates them, and an outage is REPORTED (not halted on)
    before the None. Returning None on an outage is still the chosen behaviour; the
    change is that the operator is told gate 1 did not run."""
    wp = work_path_match(cwd)
    if wp:
        return f"work-path {wp}"
    # #50: checked here rather than inside _in_git so the warning cannot fire when the
    # work-path list has ALREADY answered -- gate 1 did run in that case, and a warning
    # that also fires when nothing is wrong teaches its reader to ignore it.
    in_git, unavailable = git_signal_status(cwd)
    if unavailable:
        report_git_unavailable(cwd)
    if not in_git:
        return None
    b = load_boundary()
    email = _email(cwd)
    for dom in b["work_domains"]:
        if email.endswith("@" + dom.lower()):
            return f"user.email {email}"
    host, owner = _origin(cwd)
    if host:
        for dom in b["work_domains"]:
            d = dom.lower()
            if host == d or host.endswith("." + d):
                return f"remote host {host}"
    if host == "github.com" and owner:
        for suf in b["work_owner_suffixes"]:
            if owner.lower().endswith(suf.lower()):
                return f"github owner {owner}"
    return None


def personal_signal(cwd: Path):
    """True if the live cwd shows a positive PERSONAL signal (§4). MANIFEST-driven (R14):
    the personal identity/owner come from the manifest's own `personal` entry, never from
    literals in shipped code."""
    if not _in_git(cwd):
        return False
    entry = _personal_entry()
    if entry is None:
        return False
    if _email(cwd) == str(entry["identity"]).lower():
        return True
    host, owner = _origin(cwd)
    return (host == "github.com" and owner is not None
            and owner.lower() == str(entry["remote_owner"]).lower())


# ---------------------------------------------------------------- SEC-002 registry

def is_reparse(path: Path):
    """True iff `path` is a junction or symlink (tags that make resolve() escape the
    root). Uses the reparse tag, not Path.is_symlink() (which misses junctions); scoped
    to the two escaping tags so benign reparse points (cloud-sync placeholders, etc.) pass.

    RAISES off Windows (P6). st_reparse_tag exists only on Windows, so the
    getattr default would answer False for every path and this guard would report success
    while checking nothing -- inert rather than absent. A compatibility string alone would
    be an editorial control over a structural gap, so the tool refuses to run instead."""
    if os.name != "nt":
        raise RuntimeError(
            "aor-kb is Windows only: is_reparse() needs the Win32 st_reparse_tag field, which "
            "does not exist on this platform. A junction/symlink guard that cannot see reparse "
            "tags would pass every path silently, so this refuses rather than answering False.")
    try:
        return getattr(os.lstat(path), "st_reparse_tag", 0) in _ESCAPING_REPARSE
    except OSError:
        return False


def load_registry():
    """Parse the machine-local workspaces.jsonl -> {resolved_path: choice}
    (last-record-wins). Fail-closed on a bad choice, a missing dir, or any
    cross-choice nesting; skip an unparseable line loudly (§6)."""
    rp = registry_path()
    records = {}
    if rp.is_file():
        for lineno, ln in enumerate(rp.read_text(encoding="utf-8").splitlines(), 1):
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
                p = str(Path(rec["path"]).resolve())
                choice = rec["choice"]
            except Exception:
                # skip loudly with a locator so the operator can hand-fix the torn/edited
                # line (recovery from a bad append is an operator edit, §6).
                print(f"kb-boundary: skipping unparseable {rp.name} line {lineno}: "
                      f"{ln[:60]!r}", file=sys.stderr)
                continue
            if choice not in CHOICES:
                die(f"registry load HALT: bad choice '{choice}' for {p} (SEC-002 §6)")
            records[p] = choice
    items = list(records.items())
    for p, _ in items:
        if not Path(p).is_dir():
            die(f"registry load HALT: not a directory -- {p} (SEC-002 §6)")
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = Path(items[i][0]), Path(items[j][0])
            if a != b and (a.is_relative_to(b) or b.is_relative_to(a)):
                die(f"registry load HALT: overlapping {a} {b} -- registered workspaces "
                    "must be flat, non-nested (SEC-002 §6)")
    return records


def match_workspace(cwd: Path, records):
    """The one registered workspace containing cwd (flat rule => at most one)."""
    cwd = cwd.resolve()
    for p, choice in records.items():
        pp = Path(p)
        if cwd == pp or cwd.is_relative_to(pp):
            return {"path": p, "choice": choice}
    return None


def overlapping_registration(cwd: Path, records):
    """The already-registered workspace that nests with `cwd` (either direction), if any
    -- so register-workspace can reject a nesting conflict LOUDLY at register time rather
    than letting the next capture die at load with a disconnected overlap HALT (CODE-001)."""
    cwd = cwd.resolve()
    for p in records:
        pp = Path(p)
        if cwd != pp and (cwd.is_relative_to(pp) or pp.is_relative_to(cwd)):
            return pp
    return None


def append_registry(cwd: Path, choice, ts):
    rp = registry_path()
    rp.parent.mkdir(parents=True, exist_ok=True)
    rec = json.dumps({"path": str(cwd.resolve()), "choice": choice, "ts": ts})
    with open(rp, "a", encoding="utf-8", newline="\n") as f:
        f.write(rec + "\n")


def append_workpath(path: Path):
    wp = workpaths_path()
    wp.parent.mkdir(parents=True, exist_ok=True)
    with open(wp, "a", encoding="utf-8", newline="\n") as f:
        f.write(str(path.resolve()) + "\n")


# ---------------------------------------------------------------- SEC-002 policy

def _unassigned_halt(cwd: Path):
    """The forced-choice HALT (unregistered folder), carrying the ADVISORY name hint if
    any -- a nudge for the operator, not a decision."""
    hint = advisory_work_hint(cwd)
    extra = ("" if not hint else
             f" (note: the folder name matches an advisory work pattern '{hint}' -- decide "
             "work vs personal carefully; the pattern is only a hint, not a rule)")
    die(f"workspace-choice HALT -- folder {cwd} is not assigned to a KB; choose work|personal "
        f"and register it before any capture here can be saved{extra} (SEC-002)")


def enforce_workspace_policy(instance, cwd: Path, *, confirm_personal=False):
    """SEC-002. HALTS (exit 2) a workspace->instance mismatch. Asymmetric:
    work-direction flows; personal-direction is gated by a live work-signal veto
    (gate 1, structural) then the registered choice + a per-save confirm (gate 2,
    human). Called BEFORE the destination check for the personal direction so the
    SEC-002 reason wins over a destination misconfig."""
    cwd = cwd.resolve()

    # Component-2 override guard: any active KB_* override is announced LOUDLY, and the
    # irreversible personal direction FAILS CLOSED unless the caller explicitly acknowledges
    # the override. Placed before the registry/signal logic so a neutered guard can never
    # silently greenlight a personal write (the registry itself may be the redirected input).
    active = active_overrides()
    if active:
        warn_overrides(active)
        if instance == "personal" and not _override_ack():
            die(f"workspace-policy HALT: boundary override(s) {active} active on a PERSONAL "
                "write -- the manifest/registry/work-paths are redirected; refusing to trust a "
                "neutered guard for the irreversible direction (KB_OVERRIDE_ACK is test-only)")

    ws = match_workspace(cwd, load_registry())
    choice = ws["choice"] if ws else None

    if instance == "work":
        if choice in ("work", "both"):
            return
        if choice == "personal":
            die(f"workspace-policy HALT: personal-only -> work -- folder {cwd} feeds the "
                "personal KB; register --choice both if it does both, or capture the work "
                "note from a work folder (SEC-002)")
        _unassigned_halt(cwd)

    # instance == "personal" -- the dangerous, irreversible direction
    why = work_signal(cwd)
    if why:  # gate 1: live structural veto, independent of the registry
        die(f"workspace-policy HALT: work signal ({why}) -> personal refused -- {cwd} "
            "looks like work; work content must never reach personal GitHub (SEC-002 gate 1)")
    if choice is None:  # gate 2: choice + human confirm
        _unassigned_halt(cwd)
    if choice == "work":
        die(f"workspace-policy HALT: work-only -> personal -- folder {cwd} is registered "
            "work; refusing a personal save (SEC-002 gate 2)")
    if choice == "both" or not personal_signal(cwd):
        if not confirm_personal:
            where = "both KBs" if choice == "both" else "personal (no positive signal)"
            die(f"workspace-policy CONFIRM: -> personal -- folder {cwd} feeds {where}; "
                "confirm this note is personal, not work (it syncs to personal GitHub) -- "
                "pass --confirm-personal after the operator affirms 'yes, personal' (SEC-002)")
    return


def resolve_instance_guarded(instance, manifest, *, confirm_personal=False):
    """Workspace policy (SEC-002) THEN the destination check. The WRITE + route
    preflight entry point."""
    # Duplicated from resolve_instance ON PURPOSE -- front-loads the clean "unknown
    # --instance" message BEFORE enforce, so a bad keyword doesn't surface as a confusing
    # workspace HALT. Do not DRY this away (it would reorder the messages).
    if instance not in manifest:
        die(f"unknown --instance '{instance}' -- manifest instances: "
            f"{sorted(manifest)} (no default is permitted; §14.2.1)")
    enforce_workspace_policy(instance, Path.cwd(), confirm_personal=confirm_personal)
    return resolve_instance(instance, manifest)
