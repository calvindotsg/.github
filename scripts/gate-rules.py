#!/usr/bin/env python3
"""The merge-on-green rules that no file path can express: which changes need Calvin's review.

    gate-rules.py [--base REV] [--memory GLOB]... [--approver LOGIN]

Each repository's ./gate runs a vendored, sha256-pinned copy of this file (plan 3.4, Calvin's D5 and D6;
the orchestrator's 20 Oct ruling: one rules script, in calvindotsg/.github). It reads the change from
--base to HEAD and prints one line per rule, then

    needs-calvin=<reason; reason|none> review=<approved|missing|stale|not-needed|skipped-local>

THE RULES (each is "needs Calvin" when it fires)
    deleted   the change deletes a tracked file
    lines     over 1,000 changed lines, memory files excluded
    memory    over 20 memory files changed (a sweep)
    scan      an added line carries a secret's shape, or an email, phone number or chat id that the base
              tree does not already hold. The hit is named by file, line and kind; its value is never printed.

THE REVIEW
    In CI (GITHUB_ACTIONS=true, a pull_request or pull_request_review event), when a rule fires, the
    approver's latest review must be APPROVED on the PR's head sha; one on an older sha is stale. It reads
    the reviews with GATE_GITHUB_TOKEN, which needs pull-requests: read and nothing more. Locally the review
    is not checked: it prints review=skipped-local and the rules are advisory.

EXIT CODES
    0 nothing needs Calvin, or he approved the head, or a local run; 1 needs Calvin and not approved on the
    head (CI); 2 cannot decide (bad base, unreadable event, API failure), which fails closed.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

MAX_LINES = 1000
MAX_MEMORY = 20
DEFAULT_MEMORY = ["projects/*/memory/*", "memory/*"]
# Tests only: the API override is honoured for a loopback host alone.
API = os.environ.get("GATE_GITHUB_API", "https://api.github.com")
if urllib.parse.urlparse(API).hostname not in ("api.github.com", "127.0.0.1"):
    API = "https://api.github.com"

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
# Addresses that identify no one.
SAFE_EMAIL = re.compile(r"(@users\.noreply\.github\.com|^noreply@|@example\.(com|org|net)|\.(test|invalid|example)$)",
                        re.I)


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:200]}")
    return p.stdout


def default_base() -> str:
    """GitHub's PR checkout is a merge commit whose first parent is the base; elsewhere, main's merge-base."""
    parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(parents) == 3:
        return parents[1]
    for ref in ("origin/main", "main"):
        try:
            return git("merge-base", ref, "HEAD").strip()
        except RuntimeError:
            continue
    raise RuntimeError("no base: pass --base")


def is_memory(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def in_base(base: str, needle: str) -> bool:
    return subprocess.run(["git", "grep", "-q", "-F", "-e", needle, base, "--"], capture_output=True).returncode == 0


def rules(base: str, memory: list[str]) -> list[tuple[str, str]]:
    """[(rule, reason)] for every rule that fires."""
    hits = []
    deleted = [p for p in git("diff", "--name-only", "--no-renames", "--diff-filter=D", base, "HEAD").splitlines() if p]
    print(f"rule deleted: {len(deleted)} file(s)" + (f" ({', '.join(deleted[:5])})" if deleted else ""))
    if deleted:
        hits.append(("deleted", f"deletes {len(deleted)} tracked file(s)"))

    lines = mem = 0
    for row in git("diff", "--numstat", "--no-renames", base, "HEAD").splitlines():
        add, rm, path = row.split("\t", 2)
        if is_memory(path, memory):
            mem += 1
            continue
        lines += (int(add) if add.isdigit() else 0) + (int(rm) if rm.isdigit() else 0)
    print(f"rule lines: {lines} changed outside memory (limit {MAX_LINES})")
    if lines > MAX_LINES:
        hits.append(("lines", f"{lines} changed lines"))
    print(f"rule memory: {mem} memory file(s) changed (limit {MAX_MEMORY})")
    if mem > MAX_MEMORY:
        hits.append(("memory", f"{mem} memory files"))

    found = []
    path, n = "", 0
    for line in git("diff", "-U0", "--no-color", "--no-renames", base, "HEAD").splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            n = int(m.group(1)) if m else 0
            continue
        if not line.startswith("+") or not path:
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
                if not in_base(base, value):
                    found.append(f"{path}:{n} new {kind}")
        n += 1
    print(f"rule scan: {len(found)} hit(s)" + "".join(f"\n  {f}" for f in found[:20]))
    if found:
        hits.append(("scan", f"scan hit ({len(found)})"))
    return hits


def api(path: str, token: str) -> list:
    out: list = []
    url = API + path
    while url:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "Authorization": f"Bearer {token}",
                                                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "gate-rules"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            out += json.loads(resp.read().decode() or "[]")
            m = re.search(r'<([^>]+)>;\s*rel="next"', resp.headers.get("Link") or "")
            url = m.group(1) if m and m.group(1).startswith(API) else ""
    return out


def review(approver: str) -> str:
    """approved | missing | stale, from the approver's latest decisive review of the PR."""
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        pr = json.load(f)["pull_request"]
    head = pr["head"]["sha"]
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
        hits = rules(base, memory or DEFAULT_MEMORY)
    except (RuntimeError, ValueError) as e:
        print(f"needs-calvin=undecided review=not-needed\ngate-rules: {e}", file=sys.stderr)
        return 2
    reasons = "; ".join(r for _, r in hits) or "none"
    ci = os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_EVENT_NAME") in (
        "pull_request", "pull_request_review")
    if not hits:
        state = "not-needed"
    elif not ci:
        state = "skipped-local"
    else:
        try:
            state = review(approver)
        except (OSError, KeyError, ValueError, RuntimeError, urllib.error.URLError) as e:
            print(f"needs-calvin={reasons} review=undecided\ngate-rules: cannot read the review: {e}",
                  file=sys.stderr)
            return 2
    print(f"needs-calvin={reasons} review={state}")
    if state == "skipped-local":
        print("gate-rules: local run, review not checked (advisory)")
    return 1 if state in ("missing", "stale") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
