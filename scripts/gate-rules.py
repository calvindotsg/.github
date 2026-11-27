#!/usr/bin/env python3
"""The merge-on-green rules that no file path can express: which changes need Calvin's review.

    gate-rules.py [--base REV] [--memory GLOB]... [--approver LOGIN]

Each repository's ./gate runs a vendored, sha256-pinned copy of this file (plan 3.4, Calvin's D5 and D6;
the orchestrator's 20 Oct ruling: one rules script, in calvindotsg/.github). It reads the change from
--base to HEAD and prints one line per rule, then

    needs-calvin=<reason; reason|none> review=<approved|missing|stale|not-needed|skipped-local>
        [ judge=<reasons|none>, under a trusted judge]

A control character in a reason (a file name with a newline) prints as \\xNN, so the line stays one line.

THE RULES (each is "needs Calvin" when it fires)
    deleted   the change deletes a tracked file
    lines     over 1,000 changed lines, memory files excluded
    memory    over 20 memory files changed (a sweep)
    binaries  (informational, never fires) a binary file in the change is listed with its size at HEAD, or
              "deleted". Git counts a binary as 0 lines, so the lines rule cannot see it; there is no limit.
              Only the path and the byte count are printed, never contents.
    scan      an added line carries a secret's shape, or an email, phone number or chat id that the base
              tree does not already hold. The hit is named by file, line and kind; its value is never printed.
    codeowners  a changed path (deleted and renamed ones included) is owned by @<approver> in the repo's
              CODEOWNERS (.github/, root, then docs/), read from the base and from HEAD; plan 3.4's file paths.
              Locally this is the only place that list binds: GitHub's code-owner review needs branch protection.
    settings  a settings.json that has Claude Code's `permissions` or `hooks` at the base loses a deny rule, or a
              hook command (or the statusLine command) at the base is gone or changed at HEAD. Adding is fine.
    gitignore a .gitignore change un-ignores: it adds a `!` line that names a secret-bearing path (credentials,
              tokens, keys, sessions, state) or opens a whole tree (`/**`, `/*`), or it removes an ignore line.
    guards    a script that a hook or the statusLine runs (read from settings.json at the base and HEAD) changes
              and its test does not: a test file in its directory named for it, else any test file there.
    gatefile  the change removes or changes a line of the repo's ./gate. A line only added is fine.
    rulesfile the change touches gate-rules.py or its pin, gate-rules.sha256.
    workflows the change touches a file under .github/workflows/.

THE TRUSTED JUDGE (GATE_TRUSTED_JUDGE=1)
    Calvin, 26 Nov 2026 10:11:28 SGT (record 919abb82, "Two keys for four hits"): a change to the judge lands on
    two keys, with no review by him. When the caller sets GATE_TRUSTED_JUDGE=1, the gatefile, rulesfile, workflows
    and codeowners rules no longer need Calvin: their hits print as a tag, `judge=<reasons>`, at the end of the
    last line, and merge-green holds such a PR to its two-key path. Every other rule is unchanged, so a lost deny
    rule or hook, an un-ignored secret or an untested guard still needs him.
    The tag is honoured only when the judge that runs is the base's own: this rules file, the gate-rules.sha256
    beside it, the root ./gate and each CODEOWNERS file must be byte for byte the base's copies in the working
    tree (the caller puts them there, as the shared workflow's trusted-judge input does). If any differs, the
    request is refused with the path named and the rules need Calvin as before, so a PR that edits its own gate
    to set the variable gains nothing. So is a file in the rules file's folder that the base lacks or holds
    otherwise (Python would import it), a rules file outside the repository, and a failed comparison. Only the
    value 1 asks.

THE REVIEW
    In CI (GITHUB_ACTIONS=true, a pull_request or pull_request_review event), when a rule fires, the
    approver's latest review must be APPROVED on the PR's head sha; one on an older sha is stale. It reads
    the reviews with GATE_GITHUB_TOKEN, which needs pull-requests: read and nothing more. A PR the approver
    opened himself is self-authored: GitHub never counts an author's own review, so it says how to proceed.
    Locally the review is not checked: it prints review=skipped-local and the rules are advisory. In Actions
    under any other event there is no PR to read, so a rule that fires there cannot be decided (exit 2).

EXIT CODES
    0 nothing needs Calvin (judge tags included), or he approved the head, or a local run; 1 needs Calvin and not approved on the
    head, or self-authored (CI); 2 cannot decide (bad base, unreadable event, API failure), which fails closed.

THE BASE, when --base is not given
    In Actions under a pull_request or pull_request_review event, HEAD must be GitHub's merge of the event's
    head sha, and its first parent is the base; any other HEAD there exits 2. Everywhere else, the merge-base
    with origin/main (else main). A branch head that merged main is never measured from its first parent.
"""

import sys

# Run as a script, Python puts this file's folder first on the import path, so a file a PR adds beside it (an
# fnmatch.py, a json/ package) would run inside the judge before any check could look. Drop that entry before the
# first import that reads the path; `sys` is built in. Under -I or -P Python never adds it. No `from __future__`
# import either: it too is read from the path.
if __name__ == "__main__" and sys.path and not sys.flags.isolated and not getattr(sys.flags, "safe_path", False):
    del sys.path[0]

import fnmatch  # noqa: E402 (after the path is fixed)
import http.client  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import urllib.error
import urllib.parse
import urllib.request

MAX_LINES = 1000
MAX_MEMORY = 20
DEFAULT_MEMORY = ["projects/*/memory/*", "memory/*"]


def origin(url: str) -> tuple:
    p = urllib.parse.urlparse(url)
    return (p.scheme, p.netloc)


# Tests only: the API override is honoured for plain-http loopback alone; anything else is GitHub over https.
API = os.environ.get("GATE_GITHUB_API", "https://api.github.com")
if origin(API) != ("https", "api.github.com") and not (
        urllib.parse.urlparse(API).scheme == "http" and urllib.parse.urlparse(API).hostname == "127.0.0.1"):
    API = "https://api.github.com"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect becomes an HTTP error: urllib would carry the Authorization header to wherever it points."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)

SECRETS = [
    ("github token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})")),
    ("anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("aws key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("1password token", re.compile(r"\bops_[A-Za-z0-9_-]{40,}")),
]
IDENTIFIERS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone", re.compile(r"(?<![\w+])\+\d{1,3}[ -]?\d{3,4}[ -]?\d{4}\b|(?<![\w-])[689]\d{3}[ -]\d{4}\b")),
    ("chat id", re.compile(r"(?<![\w-])-100\d{9,13}\b")),
]
# Addresses that identify no one. Each domain is anchored at the end: a domain under example.com.evil.sg is someone's,
# and so is noreply@ at any domain not named here.
SAFE_EMAIL = re.compile(r"(^noreply@(anthropic\.com|github\.com)|@users\.noreply\.github\.com|@example\.(com|org|net)"
                        r"|\.(test|invalid|example))$", re.I)


def git(*args: str) -> str:
    # Bytes that are not UTF-8 (a font, a latin-1 file, an odd file name) must neither crash the run nor change:
    # surrogateescape carries them through, so a path read here is the same path when it goes back to git (F7).
    p = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="surrogateescape")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:200]}")
    return p.stdout


def default_base() -> str:
    """GitHub's PR checkout is a merge commit whose first parent is the base and whose second is the PR head.
    Only there is HEAD^1 the base: a branch head that merged main also has two parents, and its first is the
    old branch tip (F12). So in Actions under a PR event, HEAD must be that merge of the event's head sha, or
    the base cannot be decided; everywhere else it is main's merge-base."""
    if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_EVENT_NAME") in (
            "pull_request", "pull_request_review"):
        try:
            with open(os.environ["GITHUB_EVENT_PATH"]) as f:
                head = json.load(f)["pull_request"]["head"]["sha"]
        except (OSError, KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"cannot read the PR head from the event ({type(e).__name__}): pass --base")
        parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
        if len(parents) == 3 and parents[2] == head:
            return parents[1]
        raise RuntimeError("HEAD is not GitHub's merge of this PR's head: pass --base")
    for ref in ("origin/main", "main"):
        try:
            return git("merge-base", ref, "HEAD").strip()
        except RuntimeError:
            continue
    raise RuntimeError("no base: pass --base")


def is_memory(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def in_base(base: str, rx: re.Pattern, needle: str) -> bool:
    """True if the base holds needle as a whole identifier: rx finds exactly it on some base line, not a longer one."""
    p = subprocess.run(["git", "grep", "-h", "-F", "-e", needle, base, "--"], capture_output=True, text=True,
                       errors="replace")
    return p.returncode == 0 and any(m.group(0) == needle for line in p.stdout.split("\n") for m in rx.finditer(line))


def rules(base: str, memory: list[str], approver: str = "calvindotsg") -> list[tuple[str, str]]:
    """[(rule, reason)] for every rule that fires."""
    hits = []
    deleted = [p for p in git("diff", "--name-only", "-z", "--no-renames", "--diff-filter=D", base, "HEAD").split("\0") if p]
    print(f"rule deleted: {len(deleted)} file(s)" + (f" ({', '.join(deleted[:5])})" if deleted else ""))
    if deleted:
        hits.append(("deleted", f"deletes {len(deleted)} tracked file(s)"))

    lines = mem = 0
    binaries = []
    for row in git("diff", "--numstat", "-z", "--no-renames", base, "HEAD").split("\0"):
        if not row:
            continue
        add, rm, path = row.split("\t", 2)
        if add == "-" and rm == "-":
            try:
                size = f"{int(git('cat-file', '-s', f'HEAD:{path}').strip())} bytes"
            except (RuntimeError, ValueError):
                size = "deleted"
            binaries.append(f"rule binaries: {path} {size}")
        if is_memory(path, memory):
            mem += 1
            continue
        lines += (int(add) if add.isdigit() else 0) + (int(rm) if rm.isdigit() else 0)
    print("\n".join(binaries + [f"rule binaries: {len(binaries)} binary file(s), listed with no limit"]))
    print(f"rule lines: {lines} changed outside memory (limit {MAX_LINES})")
    if lines > MAX_LINES:
        hits.append(("lines", f"{lines} changed lines"))
    print(f"rule memory: {mem} memory file(s) changed (limit {MAX_MEMORY})")
    if mem > MAX_MEMORY:
        hits.append(("memory", f"{mem} memory files"))

    # One diff per file, named by -z (never quoted), so no header line or odd path can hide a file from the scan.
    # Before a file's first @@ every line is header; after it every line is a hunk header or carries a one-char
    # prefix, so a "+" line is always an added line. Split on newlines only: splitlines() would also break on
    # a form feed or \x1c inside a line and drop the rest of it from the scan.
    found = []
    for path in git("diff", "--name-only", "-z", "--no-renames", "--diff-filter=d", base, "HEAD").split("\0"):
        if not path:
            continue
        hunk, n = False, 0
        # --text: a file git or the PR's own .gitattributes calls binary is still read line by line.
        for line in git("--literal-pathspecs", "diff", "-U0", "--text", "--no-textconv", "--no-color", "--no-ext-diff",
                        "--no-renames", base, "HEAD", "--", path).split("\n"):
            if line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                hunk, n = True, int(m.group(1)) if m else 0
                continue
            if not hunk or not line.startswith("+"):
                continue
            text = line[1:]
            for kind, rx in SECRETS:
                if rx.search(text):
                    found.append(f"{path}:{n} {kind}")
            for kind, rx in IDENTIFIERS:
                for m in rx.finditer(text):
                    value = m.group(0)
                    if kind == "email" and SAFE_EMAIL.search(value):
                        continue
                    if not in_base(base, rx, value):
                        found.append(f"{path}:{n} new {kind}")
            n += 1
    print(f"rule scan: {len(found)} hit(s)" + "".join(f"\n  {f}" for f in found[:20]))
    if found:
        hits.append(("scan", f"scan hit ({len(found)})"))

    owned, where = codeowned(base, approver)
    print(f"rule codeowners: {len(owned)} changed path(s) owned by @{approver} in {where}"
          + "".join(f"\n  {p}" for p in owned[:20]))
    if owned:
        hits.append(("codeowners", f"codeowners: {len(owned)} path(s) ({', '.join(owned[:3])})"))

    changed = [p for p in git("diff", "--name-only", "-z", "--no-renames", base, "HEAD").split("\0") if p]
    for name, found_here, reason in config_safety(base, changed):
        print(f"rule {name}: {len(found_here)} hit(s)" + "".join(f"\n  {f}" for f in found_here[:20]))
        if found_here:
            hits.append((name, f"{reason} ({', '.join(found_here[:3])})"))
    return hits


# Calvin, 25 Nov 2026 10:49:19 SGT (record 5315bcee, "option 2"): with CODEOWNERS emptied, these changes still need
# him. They live here, in the pinned rules, so a PR to a config repo cannot weaken the check that judges it without
# also tripping `rulesfile`.
SECRET_WORDS = re.compile(r"credential|secret|token|passw|auth|cookie|\.env\b|\.pem\b|\.key\b|\.p12\b|id_rsa|id_ed25519"
                          r"|keychain|1password|settings\.local|session|history|(^|/)state(/|$)|\.jsonl\b", re.I)
SCRIPT_REF = re.compile(r"(?:\$HOME|\$\{HOME\}|~)/\.claude/([^\s'\"]+)")


def show(rev: str, path: str):
    """The file's text at rev, or None when it is absent there."""
    try:
        return git("show", f"{rev}:{path}")
    except RuntimeError:
        return None


def claude_settings(text):
    """(deny rules, hook commands, statusLine command) of a Claude Code settings.json, or None if it is not one."""
    try:
        data = json.loads(text) if text is not None else None
    except ValueError:
        return "unreadable"
    if not isinstance(data, dict) or not ("permissions" in data or "hooks" in data):
        return None
    perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    deny = {d for d in perms.get("deny") or [] if isinstance(d, str)}
    hooks = set()
    for event, groups in (data.get("hooks") or {}).items():
        for g in groups if isinstance(groups, list) else []:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                if isinstance(h, dict):
                    hooks.add((event, g.get("matcher", ""), h.get("command", "")))
    status = data.get("statusLine") if isinstance(data.get("statusLine"), dict) else {}
    return deny, hooks, status.get("command", "")


def guard_paths(commands) -> set:
    """Repo paths of the scripts the commands run. The settings file sits at the repo root, which is ~/.claude."""
    return {m.group(1) for c in commands for m in SCRIPT_REF.finditer(c or "")}


def norm(name: str) -> str:
    return name.lower().replace("-", "_")


def config_safety(base: str, changed: list) -> list:
    """[(rule, hits, reason)] for the six config-safety rules, in order, fired or not."""
    out = []

    settings, guards = [], set()
    for path in sorted({p for p in changed if p.rsplit("/", 1)[-1] == "settings.json"}
                       | {p for p in ("settings.json",) if show(base, p) is not None}):
        before, after = claude_settings(show(base, path)), claude_settings(show("HEAD", path))
        for side in (before, after):
            if isinstance(side, tuple) and "/" not in path:
                guards |= guard_paths([c for _, _, c in side[1]] + [side[2]])
        if path not in changed or before is None:
            continue
        if before == "unreadable":
            continue
        if not isinstance(after, tuple):
            settings.append(f"{path} is no longer Claude Code settings")
            continue
        settings += [f"{path} deny rule lost: {d}" for d in sorted(before[0] - after[0])]
        settings += [f"{path} hook lost or changed: {e} {m!r}" for e, m, _ in sorted(before[1] - after[1])]
        if before[2] and before[2] != after[2]:
            settings.append(f"{path} statusLine command changed")
    out.append(("settings", settings, "settings.json weakened"))

    ignores = []
    for path in [p for p in changed if p.rsplit("/", 1)[-1] == ".gitignore"]:
        for line in git("--literal-pathspecs", "diff", "-U0", "--no-color", "--no-ext-diff", base, "HEAD", "--",
                        path).split("\n"):
            if line.startswith(("+++", "---")) or not line[:1] in ("+", "-"):
                continue
            rule = line[1:].strip()
            if not rule or rule.startswith("#"):
                continue
            if line[0] == "+" and rule.startswith("!") and (SECRET_WORDS.search(rule) or rule.endswith(("/**", "/*"))):
                ignores.append(f"{path} un-ignores {rule}")
            elif line[0] == "-" and not rule.startswith("!"):
                ignores.append(f"{path} drops the ignore {rule}")
    out.append(("gitignore", ignores, ".gitignore un-ignores"))

    untested = []
    head_files = [p for p in git("ls-files", "-z").split("\0") if p]
    for g in sorted(guards & set(changed)):
        folder = g.rsplit("/", 1)[0] if "/" in g else ""
        stem = norm(g.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        here = lambda p: (p.rsplit("/", 1)[0] if "/" in p else "") == folder and p.rsplit("/", 1)[-1].startswith("test")
        named = [p for p in head_files if here(p) and stem in norm(p.rsplit("/", 1)[-1])]
        if not any(p in changed for p in (named or [p for p in head_files + changed if here(p)])):
            untested.append(f"{g} (its test: {named[0] if named else 'any test file beside it'})")
    out.append(("guards", untested, "hook guard changed without its test"))

    gate_lines = []
    if "gate" in changed:
        for line in git("diff", "-U0", "--no-color", "--no-ext-diff", base, "HEAD", "--", "gate").split("\n"):
            if line.startswith("-") and not line.startswith("---"):
                gate_lines.append(f"gate: {line[1:].strip()[:80]}")
    out.append(("gatefile", gate_lines, "gate line removed or changed"))

    rules_files = [p for p in changed if p.rsplit("/", 1)[-1] in ("gate-rules.py", "gate-rules.sha256")]
    out.append(("rulesfile", rules_files, "gate rules or pin changed"))

    flows = [p for p in changed if p.startswith(".github/workflows/")]
    out.append(("workflows", flows, "CI workflow changed"))
    return out


CODEOWNERS_FILES = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")   # GitHub's order: the first one found
JUDGE_RULES = ("gatefile", "rulesfile", "workflows", "codeowners")   # tags, not Calvin, under a trusted judge


def blob(rev: str, path: str):
    """The blob id of path at rev, or None when it is absent there."""
    try:
        return git("rev-parse", "--verify", "--quiet", f"{rev}:{path}").strip() or None
    except RuntimeError:
        return None


def trusted_judge(base: str):
    """(True, "") when GATE_TRUSTED_JUDGE=1 and the judge in the working tree is the base's own; (False, why) when
    the variable is set but the judge differs; (False, "") when it is not set."""
    if os.environ.get("GATE_TRUSTED_JUDGE") != "1":
        return False, ""
    top = git("rev-parse", "--show-toplevel").strip()
    me = os.path.realpath(__file__)
    rel = os.path.relpath(me, os.path.realpath(top))
    if rel.startswith(".." + os.sep) or rel == "..":
        return False, f"this rules file is outside the repository ({me})"
    folder = os.path.dirname(rel)
    # The walk below covers this file and its pin when they exist; the pin is named so a deleted one counts too.
    paths = [os.path.join(folder, "gate-rules.sha256"), "gate", *CODEOWNERS_FILES]
    # Python puts this file's folder first on the import path, so a file the PR added beside it (an fnmatch.py, a
    # json/ package) would run inside the judge: every file there, untracked ones included, must be the base's too.
    # A __pycache__ is skipped: Python loads a cached .pyc only for a source file beside it.
    for root, dirs, files in os.walk(os.path.join(top, folder)):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        paths += [os.path.relpath(os.path.join(root, f), top) for f in sorted(files)]
        paths += [os.path.relpath(os.path.join(root, d), top) for d in dirs if os.path.islink(os.path.join(root, d))]
    for path in paths:
        here = os.path.join(top, path)
        now = git("hash-object", "--no-filters", "--", here).strip() \
            if os.path.isfile(here) and not os.path.islink(here) else None if not os.path.lexists(here) else "other"
        if now != blob(base, path):
            return False, f"{path} is not the base's copy"
    return True, ""


def plain(text: str) -> str:
    """A reason as one line: a path with a newline or other control character in it must not start a line of its
    own, which ./gate could read as the last needs-calvin line."""
    return re.sub(r"[\x00-\x1f\x7f]", lambda m: f"\\x{ord(m.group(0)):02x}", text)


def owner_pattern(pat: str) -> re.Pattern:
    """A CODEOWNERS pattern (gitignore rules) as a regex on a repo path. A leading or inner slash anchors it at the
    root, else it matches at any depth; a trailing slash matches only under a directory; a pattern whose last segment
    has no wildcard also matches everything under a directory it names; `*` and `?` stay inside one path segment
    (so `docs/*` is not `docs/a/b.md`, as GitHub documents), `**` crosses them."""
    anchored = "/" in pat.rstrip("/")
    is_dir = pat.endswith("/")
    core, out, i = pat.strip("/"), "", 0
    while i < len(core):
        if core.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif core.startswith("/**", i) and i + 3 == len(core):
            out, i = out + "(?:/.*)?", i + 3
        elif core.startswith("**", i):
            out, i = out + ".*", i + 2
        elif core[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif core[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(core[i]), i + 1
    tail = "/.*$" if is_dir else "$" if re.search(r"[*?]", core.rsplit("/", 1)[-1]) else "(?:/.*)?$"
    return re.compile(("^" if anchored else "^(?:.*/)?") + out + tail)


def owner_rules(rev: str) -> tuple[str, list[tuple[re.Pattern, list[str]]]]:
    """(file name, [(pattern, owners)]) from the first CODEOWNERS file in `rev`'s tree; ("", []) if it has none."""
    for name in CODEOWNERS_FILES:
        try:
            text = git("show", f"{rev}:{name}")
        except RuntimeError:
            continue
        rows = []
        for line in text.split("\n"):
            words = line.split("#", 1)[0].split()
            if words:
                rows.append((owner_pattern(words[0]), [w.lower() for w in words[1:]]))
        return name, rows
    return "", []


def codeowned(base: str, approver: str) -> tuple[list[str], str]:
    """Changed paths (added, changed, deleted, both sides of a rename) that the approver owns by CODEOWNERS, in the
    base's file or the head's: a PR cannot drop its own path from the list, and the PR adding the file is held by
    it. The last matching line decides a path, and a line with no owners leaves it unowned, as on GitHub."""
    me = "@" + approver.lower()
    files = [owner_rules(rev) for rev in (base, "HEAD")]
    where = " and ".join(sorted({f"{name} at {rev}" for (name, _), rev in zip(files, ("base", "HEAD")) if name})) \
        or "no CODEOWNERS file"
    owned = []
    for path in git("diff", "--name-only", "-z", "--no-renames", base, "HEAD").split("\0"):
        if not path:
            continue
        for _, rows in files:
            last_owners = None
            for rx, owners in rows:
                if rx.match(path):
                    last_owners = owners
            if last_owners and me in last_owners:
                owned.append(path)
                break
    return owned, where


def api(path: str, token: str) -> list:
    out: list = []
    url = API + path
    while url:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "Authorization": f"Bearer {token}",
                                                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "gate-rules"})
        with OPENER.open(req, timeout=30) as resp:
            page = json.loads(resp.read().decode() or "[]")
            if not isinstance(page, list):
                raise ValueError("the reviews API did not return a list")
            out += page
            m = re.search(r'<([^>]+)>;\s*rel="next"', resp.headers.get("Link") or "")
            # The token goes only to the API's own scheme and host: a next link elsewhere is an error, not a skip.
            if m and origin(m.group(1)) != origin(API):
                raise ValueError("the next page is outside the API")
            url = m.group(1) if m else ""
    return out


def review(approver: str) -> str:
    """approved | missing | stale | self-authored, from the approver's latest decisive review of the PR."""
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        pr = json.load(f)["pull_request"]
    head = pr["head"]["sha"]
    if pr["user"]["login"] == approver:
        return "self-authored"
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ.get("GATE_GITHUB_TOKEN") or ""
    if not token:
        raise RuntimeError("GATE_GITHUB_TOKEN is not set")
    mine = [r for r in api(f"/repos/{repo}/pulls/{pr['number']}/reviews?per_page=100", token)
            if (r.get("user") or {}).get("login") == approver
            and r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")]
    if not mine or mine[-1]["state"] != "APPROVED":
        return "missing"
    return "approved" if mine[-1].get("commit_id") == head else "stale"


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(errors="backslashreplace")   # such a path prints escaped, never as a crash (F7)
    sys.stderr.reconfigure(errors="backslashreplace")
    base, memory, approver = None, [], "calvindotsg"
    i = 0
    while i < len(argv):
        if argv[i] in ("--base", "--memory", "--approver") and i + 1 < len(argv):
            if argv[i] == "--base":
                base = argv[i + 1]
            elif argv[i] == "--memory":
                memory.append(argv[i + 1])
            else:
                approver = argv[i + 1]
            i += 2
        else:
            print(f"gate-rules: unknown argument {argv[i]}\n" + __doc__.split("\n\n")[1], file=sys.stderr)
            return 2
    try:
        base = base or default_base()
        hits = rules(base, memory or DEFAULT_MEMORY, approver)
    except (RuntimeError, ValueError) as e:
        print(f"needs-calvin=undecided review=not-needed\ngate-rules: {e}", file=sys.stderr)
        return 2
    try:
        trusted, refused = trusted_judge(base)
    except RuntimeError as e:
        trusted, refused = False, f"cannot compare the judge with the base: {e}"
    if refused:
        print(f"gate-rules: trusted judge refused: {refused}; the judge rules need Calvin")
    judged = [h for h in hits if trusted and h[0] in JUDGE_RULES]
    hits = [h for h in hits if h not in judged]
    tag = f" judge={plain('; '.join(r for _, r in judged)) or 'none'}" if trusted else ""
    if trusted:
        print("gate-rules: trusted judge: the base's judge runs; gatefile, rulesfile, workflows and codeowners "
              "print as tags")
    reasons = plain("; ".join(r for _, r in hits)) or "none"
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    event = os.environ.get("GITHUB_EVENT_NAME")
    if not hits:
        state = "not-needed"
    elif not actions:
        state = "skipped-local"
    elif event not in ("pull_request", "pull_request_review"):
        print(f"needs-calvin={reasons} review=undecided{tag}\ngate-rules: a {event} run has no PR whose review could "
              "clear this", file=sys.stderr)
        return 2
    else:
        try:
            state = review(approver)
        except (OSError, KeyError, TypeError, AttributeError, ValueError, RuntimeError, urllib.error.URLError,
                http.client.HTTPException) as e:
            print(f"needs-calvin={reasons} review=undecided{tag}\ngate-rules: cannot read the review: {e}",
                  file=sys.stderr)
            return 2
    print(f"needs-calvin={reasons} review={state}{tag}")
    if state == "skipped-local":
        print("gate-rules: local run, review not checked (advisory)")
    if state == "self-authored":
        print(f"gate-rules: opened as {approver}, who cannot approve his own PR: re-open it via gh-bot, "
              "or before phase 6 he merges it himself")
    return 1 if state in ("missing", "stale", "self-authored") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
