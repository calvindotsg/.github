#!/usr/bin/env python3
"""Tests for scripts/gate-rules.py, against throwaway git repositories and a fake GitHub API on 127.0.0.1.

    /usr/bin/python3 scripts/test-gate-rules.py            # the cases
    /usr/bin/python3 scripts/test-gate-rules.py --mutate   # also require each planted defect to fail a case

Every rule has a red fixture and the case just under its limit; the review has approved, stale (approved on an
older sha), overridden, missing, local and API-failure cases. No real token, network or repository is used.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "gate-rules.py"
PASSES = 0
FAILS: list[str] = []
FAKE_SECRET = "ghp_" + "A1b2C3d4" * 5  # shaped like a token, valid nowhere


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print(f"  ok   {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL {label}" + (f"\n       got: {detail[:600]}" if detail else ""))


class Api(http.server.BaseHTTPRequestHandler):
    reviews: list = []
    calls: list = []
    status = 200

    def log_message(self, *a):
        pass

    def do_GET(self):
        Api.calls.append({"path": self.path, "auth": self.headers.get("Authorization", "")})
        raw = json.dumps(Api.reviews if Api.status == 200 else {"message": "boom"}).encode()
        self.send_response(Api.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Rig:
    def __init__(self, tool: Path):
        self.tool = tool
        self.dir = Path(tempfile.mkdtemp(prefix="gate-rules-"))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def repo(self, base: dict, change: dict, delete: tuple = ()) -> tuple[Path, str]:
        """A repo with a base commit of `base` files, then one commit applying `change` and `delete`."""
        r = self.dir / f"r{len(list(self.dir.iterdir()))}"
        r.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True, check=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        for p, text in base.items():
            (r / p).parent.mkdir(parents=True, exist_ok=True)
            (r / p).write_text(text)
        g("add", "-A")
        g("commit", "-qm", "base", "--allow-empty")
        b = g("rev-parse", "HEAD").stdout.strip()
        for p, text in change.items():
            (r / p).parent.mkdir(parents=True, exist_ok=True)
            (r / p).write_text(text)
        for p in delete:
            (r / p).unlink()
        g("add", "-A")
        g("commit", "-qm", "change", "--allow-empty")
        return r, b

    def run(self, repo: Path, base: str, ci: bool = False, head: str = "", **extra: str):
        Api.calls = []
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.dir)}
        if ci:
            ev = self.dir / "event.json"
            ev.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": head or "headsha"}}}))
            env.update({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(ev),
                        "GITHUB_REPOSITORY": "calvindotsg/x", "GATE_GITHUB_TOKEN": "fake-token",
                        "GATE_GITHUB_API": f"http://127.0.0.1:{self.server.server_address[1]}"})
        env.update(extra)
        return subprocess.run([sys.executable, str(self.tool), "--base", base], cwd=repo, env=env,
                              capture_output=True, text=True, timeout=60)

    def close(self):
        self.server.shutdown()
        shutil.rmtree(self.dir, ignore_errors=True)


def last(p) -> str:
    lines = [l for l in p.stdout.splitlines() if l.startswith("needs-calvin=")]
    return lines[-1] if lines else ""


def run_suite(tool: Path) -> None:
    r = Rig(tool)
    try:
        print("-- nothing to flag --")
        repo, b = r.repo({"a.txt": "a\n"}, {"a.txt": "a\nb\n"})
        p = r.run(repo, b)
        check("a small edit needs no one (exit 0)", p.returncode == 0 and last(p) == "needs-calvin=none review=not-needed",
              p.stdout + p.stderr)
        p = r.run(repo, b, ci=True)
        check("...and in CI reads no reviews", p.returncode == 0 and not Api.calls, str(Api.calls))

        print("-- deleted --")
        repo, b = r.repo({"a.txt": "a\n", "b.txt": "b\n"}, {}, delete=("b.txt",))
        p = r.run(repo, b)
        check("deleting a tracked file needs Calvin", "deletes 1 tracked file(s)" in last(p), p.stdout)
        check("...locally advisory: exit 0, review skipped and said so",
              p.returncode == 0 and "review=skipped-local" in last(p) and "review not checked" in p.stdout, p.stdout)
        repo, b = r.repo({"a.txt": "a\n"}, {"c.txt": "c\n"})
        check("adding a file is not a delete", "deletes" not in last(r.run(repo, b)))

        print("-- lines --")
        repo, b = r.repo({}, {"big.txt": "x\n" * 1001})
        check("1,001 changed lines need Calvin", "1001 changed lines" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"big.txt": "x\n" * 1000})
        check("1,000 do not", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({}, {"projects/p/memory/m.md": "x\n" * 1500})
        check("memory lines are not counted", "changed lines" not in last(r.run(repo, b)))

        print("-- memory --")
        repo, b = r.repo({}, {f"projects/p/memory/m{i}.md": "x\n" for i in range(21)})
        check("21 memory files need Calvin", "21 memory files" in last(r.run(repo, b)))
        repo, b = r.repo({}, {f"projects/p/memory/m{i}.md": "x\n" for i in range(20)})
        check("20 do not", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")

        print("-- scan --")
        repo, b = r.repo({"a.txt": "a\n"}, {"conf.sh": "x=1\ntoken=" + FAKE_SECRET + "\n"})
        p = r.run(repo, b)
        check("a token-shaped string needs Calvin", "scan hit (1)" in last(p), p.stdout)
        check("...named by file and line", "conf.sh:2 github token" in p.stdout, p.stdout)
        check("...and its value is never printed", FAKE_SECRET not in p.stdout + p.stderr, p.stdout)
        repo, b = r.repo({"a.txt": "a\n"}, {"b.md": "write to jane.doe@corp.sg\n"})
        check("a new email needs Calvin", "scan hit" in last(r.run(repo, b)))
        repo, b = r.repo({"a.txt": "jane.doe@corp.sg\n"}, {"b.md": "jane.doe@corp.sg again\n"})
        check("an email the base already holds does not", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({}, {"b.md": "68703834+calvindotsg@users.noreply.github.com, noreply@anthropic.com\n"})
        check("noreply addresses do not", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({}, {"b.md": "call +65 9123 4567\n"})
        check("a new phone number needs Calvin", "scan hit" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "chat -1001234567890\n"})
        check("a new chat id needs Calvin", "scan hit" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "-----BEGIN RSA PRIVATE KEY-----\n"})
        check("a private key header needs Calvin", "scan hit" in last(r.run(repo, b)))
        repo, b = r.repo({"b.md": "token=" + FAKE_SECRET + "\nwrite jane@corp.sg\n"}, {"b.md": "ok\n"})
        check("only added lines are scanned (removing a secret is no hit)",
              last(r.run(repo, b)) == "needs-calvin=none review=not-needed")

        print("-- the review, in CI --")
        repo, b = r.repo({"a.txt": "a\n", "b.txt": "b\n"}, {}, delete=("b.txt",))
        cal = {"login": "calvindotsg"}
        Api.status = 200
        Api.reviews = []
        p = r.run(repo, b, ci=True, head="H2")
        check("no review: fails (exit 1, review=missing)", p.returncode == 1 and "review=missing" in last(p),
              p.stdout + p.stderr)
        check("...reading this PR's reviews with the job token",
              Api.calls and "/repos/calvindotsg/x/pulls/7/reviews" in Api.calls[0]["path"]
              and Api.calls[0]["auth"] == "Bearer fake-token", str(Api.calls))
        Api.reviews = [{"user": cal, "state": "APPROVED", "commit_id": "H2"}]
        p = r.run(repo, b, ci=True, head="H2")
        check("approved on the head: passes (exit 0)", p.returncode == 0 and "review=approved" in last(p),
              p.stdout + p.stderr)
        Api.reviews = [{"user": cal, "state": "APPROVED", "commit_id": "H1"}]
        p = r.run(repo, b, ci=True, head="H2")
        check("approved on an older sha: fails (exit 1, review=stale)",
              p.returncode == 1 and "review=stale" in last(p), p.stdout + p.stderr)
        Api.reviews = [{"user": cal, "state": "APPROVED", "commit_id": "H2"},
                       {"user": cal, "state": "CHANGES_REQUESTED", "commit_id": "H2"}]
        check("a later request for changes overrides the approval",
              r.run(repo, b, ci=True, head="H2").returncode == 1)
        Api.reviews = [{"user": cal, "state": "APPROVED", "commit_id": "H2"},
                       {"user": cal, "state": "COMMENTED", "commit_id": "H2"}]
        check("a later comment does not", r.run(repo, b, ci=True, head="H2").returncode == 0)
        Api.reviews = [{"user": {"login": "someone-else"}, "state": "APPROVED", "commit_id": "H2"}]
        check("another account's approval does not count", r.run(repo, b, ci=True, head="H2").returncode == 1)
        Api.status = 500
        p = r.run(repo, b, ci=True, head="H2")
        check("the API failing fails closed (exit 2)", p.returncode == 2 and "cannot read the review" in p.stderr,
              p.stderr)
        Api.status = 200
        p = r.run(repo, b, ci=True, head="H2", GATE_GITHUB_TOKEN="")
        check("no token fails closed (exit 2)", p.returncode == 2, p.stderr)
        p = r.run(repo, b, ci=True, head="H2", GATE_GITHUB_API="http://evil.example")
        check("a non-loopback API override is ignored (no call to the fake)", not Api.calls, str(Api.calls))

        print("-- the base --")
        p = subprocess.run([sys.executable, str(tool), "--base", "no-such-rev"], cwd=repo, capture_output=True,
                           text=True, env={"PATH": "/usr/bin:/bin"})
        check("a bad base fails closed (exit 2)", p.returncode == 2 and "undecided" in p.stderr, p.stderr)
    finally:
        r.close()


MUTATIONS = [
    ("deletes ignored", "    if deleted:\n        hits.append", "    if False:\n        hits.append"),
    ("line limit off by one", "    if lines > MAX_LINES:", "    if lines >= MAX_LINES:"),
    ("memory counted in lines", "            mem += 1\n            continue\n", "            mem += 1\n"),
    ("memory limit off by one", "    if mem > MAX_MEMORY:", "    if mem >= MAX_MEMORY:"),
    ("secret scan off", "            if rx.search(text):\n                found.append",
     "            if False:\n                found.append"),
    ("identifiers in the base not excused", "                if not in_base(base, value):",
     "                if True:"),
    ("removed lines scanned too", "        if not line.startswith(\"+\") or not path:",
     "        if not path:"),
    ("a stale approval accepted", "    return \"approved\" if mine[-1].get(\"commit_id\") == head else \"stale\"",
     "    return \"approved\""),
    ("first review, not latest, decides", "mine[-1][\"state\"] != \"APPROVED\"", "mine[0][\"state\"] != \"APPROVED\""),
    ("any reviewer counts", "            if (r.get(\"user\") or {}).get(\"login\") == approver\n            and ",
     "            if "),
    ("API failure passes", "            return 2\n    print(f\"needs-calvin={reasons} review={state}\")",
     "            return 0\n    print(f\"needs-calvin={reasons} review={state}\")"),
    ("stale exits 0", "    return 1 if state in (\"missing\", \"stale\") else 0", "    return 1 if state == \"missing\" else 0"),
    ("the secret value printed", "                found.append(f\"{path}:{n} {kind}\")",
     "                found.append(f\"{path}:{n} {kind} {text}\")"),
]


def run_mutations() -> int:
    src = TOOL.read_text()
    survivors = 0
    for title, old, new in MUTATIONS:
        if old not in src:
            print(f"STALE    {title}: anchor not found")
            survivors += 1
            continue
        d = Path(tempfile.mkdtemp(prefix="gate-rules-mut-"))
        try:
            (d / "gate-rules.py").write_text(src.replace(old, new, 1))
            p = subprocess.run([sys.executable, __file__, "--tool", str(d / "gate-rules.py")], capture_output=True,
                               text=True, timeout=600)
            reds = p.stdout.count("  FAIL ")
            if p.returncode != 0 and reds:
                print(f"caught   ({reds} red) {title}")
            else:
                print(f"SURVIVED {title}")
                survivors += 1
        finally:
            shutil.rmtree(d, ignore_errors=True)
    print(f"\n{len(MUTATIONS) - survivors}/{len(MUTATIONS)} mutations caught")
    return 1 if survivors else 0


def main(argv: list[str]) -> int:
    if "--mutate" in argv:
        return run_mutations()
    tool = Path(argv[argv.index("--tool") + 1]) if "--tool" in argv else TOOL
    run_suite(tool)
    print(f"\n{PASSES} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
