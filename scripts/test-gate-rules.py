#!/usr/bin/env python3
"""Tests for scripts/gate-rules.py, against throwaway git repositories and a fake GitHub API on 127.0.0.1.

    /usr/bin/python3 scripts/test-gate-rules.py            # the cases
    /usr/bin/python3 scripts/test-gate-rules.py --mutate   # also require each planted defect to fail a case

Every rule has a red fixture and the case just under its limit; the review has approved, stale (approved on an
older sha), overridden, missing, local and API-failure cases. No real token, network or repository is used.
"""

from __future__ import annotations

import http.server
import importlib.util
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
    """The first page is `reviews`; `link` adds a Link header to it naming `page2` (path or full URL), which
    then serves `reviews2`. `body` replaces the JSON; `redirect` answers 302 to that URL instead."""
    reviews: list = []
    reviews2: list = []
    calls: list = []
    status = 200
    link = ""
    body = None
    redirect = ""

    def log_message(self, *a):
        pass

    def do_GET(self):
        Api.calls.append({"path": self.path, "auth": self.headers.get("Authorization", "")})
        if Api.redirect:
            self.send_response(302)
            self.send_header("Location", Api.redirect)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        page2 = self.path.startswith("/page2")
        data = Api.reviews2 if page2 else Api.reviews
        if Api.body is not None:
            data = Api.body
        raw = json.dumps(data if Api.status == 200 else {"message": "boom"}).encode()
        self.send_response(Api.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if Api.link and not page2:
            self.send_header("Link", f'<{Api.link}>; rel="next"')
        self.end_headers()
        self.wfile.write(raw)


def reset_api() -> None:
    Api.reviews, Api.reviews2, Api.status, Api.link, Api.body, Api.redirect = [], [], 200, "", None, ""


class Rig:
    def __init__(self, tool: Path):
        self.tool = tool
        self.dir = Path(tempfile.mkdtemp(prefix="gate-rules-"))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
        # Another origin (another port), logging into the same Api.calls: a call here is the token leaving the API.
        self.other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
        for srv in (self.server, self.other):
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    def api(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def elsewhere(self) -> str:
        return f"http://127.0.0.1:{self.other.server_address[1]}"

    def repo(self, base: dict, change: dict, delete: tuple = (), move: tuple = ()) -> tuple[Path, str]:
        """A repo with a base commit of `base` files, then one commit applying `change`, `delete` and `move`
        (pairs of old, new path)."""
        r = self.dir / f"r{len(list(self.dir.iterdir()))}"
        r.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True, check=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        for p, text in base.items():
            (r / p).parent.mkdir(parents=True, exist_ok=True)
            (r / p).write_bytes(text) if isinstance(text, bytes) else (r / p).write_text(text)
        g("add", "-A")
        g("commit", "-qm", "base", "--allow-empty")
        b = g("rev-parse", "HEAD").stdout.strip()
        for p, text in change.items():
            (r / p).parent.mkdir(parents=True, exist_ok=True)
            (r / p).write_bytes(text) if isinstance(text, bytes) else (r / p).write_text(text)
        for p in delete:
            (r / p).unlink()
        for old, new in move:
            g("mv", old, new)
        g("add", "-A")
        g("commit", "-qm", "change", "--allow-empty")
        return r, b

    def run(self, repo: Path, base: str, ci: bool = False, head: str = "", author: str = "calvindotsg-agent[bot]",
            event=None, args: tuple = (), tool=None, **extra: str):
        Api.calls = []
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.dir)}
        if ci:
            ev = self.dir / "event.json"
            ev.write_text(json.dumps(event if event is not None else {"pull_request": {
                "number": 7, "head": {"sha": head or "headsha"}, "user": {"login": author}}}))
            env.update({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(ev),
                        "GITHUB_REPOSITORY": "calvindotsg/x", "GATE_GITHUB_TOKEN": "fake-token",
                        "GATE_GITHUB_API": f"http://127.0.0.1:{self.server.server_address[1]}"})
        env.update(extra)
        return subprocess.run([sys.executable, str(tool or self.tool), *(["--base", base] if base is not None else []), *args],
                              cwd=repo, env=env,
                              capture_output=True, text=True, timeout=60)

    def close(self):
        self.server.shutdown()
        self.other.shutdown()
        shutil.rmtree(self.dir, ignore_errors=True)


def last(p) -> str:
    lines = [l for l in p.stdout.splitlines() if l.startswith("needs-calvin=")]
    return lines[-1] if lines else ""


def api_seen(tool: Path, value: str) -> str:
    """The API the tool settles on for GATE_GITHUB_API=value, read by importing it (main() does not run)."""
    old = os.environ.get("GATE_GITHUB_API")
    os.environ["GATE_GITHUB_API"] = value
    try:
        spec = importlib.util.spec_from_file_location("gate_rules_under_test", tool)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.API
    finally:
        if old is None:
            del os.environ["GATE_GITHUB_API"]
        else:
            os.environ["GATE_GITHUB_API"] = old


def merges(r: Rig) -> tuple[Path, Path, str]:
    """Two repos sharing one shape (F12). main: base B, then M deletes gone.txt. Branch feat: B, then F edits a.txt.
    `local` checks out a feat head that merged main: parents F, M; its HEAD^1 is the old branch tip, so a base of
    HEAD^1 counts main's deletion. `pr` is GitHub's checkout: a detached merge whose parents are M, F, with no main
    ref left, so only HEAD^1 can give the base. Returns (local, pr, F's sha)."""
    out = []
    for kind in ("local", "pr"):
        d = r.dir / f"m-{kind}"
        d.mkdir()
        g = lambda *a: subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, check=True).stdout
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@example.com")
        g("config", "user.name", "t")
        (d / "a.txt").write_text("a\n")
        (d / "gone.txt").write_text("g\n")
        g("add", "-A")
        g("commit", "-qm", "base")
        g("checkout", "-qb", "feat")
        (d / "a.txt").write_text("a\nb\n")
        g("commit", "-qam", "feature")
        f = g("rev-parse", "HEAD").strip()
        g("checkout", "-q", "main")
        g("rm", "-q", "gone.txt")
        g("commit", "-qm", "main moves on")
        if kind == "local":
            g("checkout", "-q", "feat")
            g("merge", "-q", "--no-ff", "-m", "merge main", "main")
        else:
            g("merge", "-q", "--no-ff", "-m", "GitHub's merge", "feat")
            g("checkout", "-q", "--detach")
            g("branch", "-qD", "main", "feat")
        out.append(d)
    return out[0], out[1], f


def commit_all(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "more"], check=True, capture_output=True)


def run_suite(tool: Path) -> None:
    print("-- the API override --")
    gh = "https://api.github.com"
    for value, want in ((gh, gh), ("http://127.0.0.1:8080", "http://127.0.0.1:8080"), ("http://api.github.com", gh),
                        ("https://api.github.com.evil.example", gh), ("https://127.0.0.1:8080", gh),
                        ("http://evil.example", gh), ("https://api.github.com:8443", gh)):
        got = api_seen(tool, value)
        check(f"GATE_GITHUB_API={value} -> {want}", got == want, got)
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
        repo, b = r.repo({"a.txt": "a\n" * 20}, {}, move=(("a.txt", "b.txt"),))
        check("a rename counts as a delete", "deletes 1 tracked file(s)" in last(r.run(repo, b)))

        print("-- lines --")
        repo, b = r.repo({}, {"big.txt": "x\n" * 1001})
        check("1,001 changed lines need Calvin", "1001 changed lines" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"big.txt": "x\n" * 1000})
        check("1,000 do not", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({}, {"projects/p/memory/m.md": "x\n" * 1500})
        check("memory lines are not counted", "changed lines" not in last(r.run(repo, b)))

        print("-- binaries --")
        png = b"\x89PNG\r\n\x1a\n\x00" * 50000
        repo, b = r.repo({"old.bin": b"\x00\x01" * 10}, {"img/big.png": png, "new.bin": b"\x00" * 7}, delete=("old.bin",))
        p = r.run(repo, b)
        check("a binary is listed with its size", f"rule binaries: img/big.png {len(png)} bytes\n" in p.stdout, p.stdout)
        check("...a deleted one as deleted", "rule binaries: old.bin deleted\n" in p.stdout, p.stdout)
        check("...with a count line", "rule binaries: 3 binary file(s), listed with no limit" in p.stdout, p.stdout)
        check("...and no limit: a 450 KB binary fires nothing new", "binary" not in last(p) and "changed lines" not in last(p),
              last(p))
        repo, b = r.repo({"a.txt": "a\n"}, {"b.txt": "b\n"})
        check("no binary: count is 0", "rule binaries: 0 binary file(s)" in r.run(repo, b).stdout)
        repo, b = r.repo({}, {"c.bin": b"\x00" * 5})
        p = r.run(repo, b)
        check("a lone binary leaves needs-calvin=none", last(p) == "needs-calvin=none review=not-needed", p.stdout)
        check("...and its bytes are never printed", "\x00" not in p.stdout, repr(p.stdout))

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
        repo, b = r.repo({"a.txt": "ajane.doe@corp.sg, jane.doe@corp.sg.au\n"}, {"b.md": "jane.doe@corp.sg\n"})
        check("an email the base holds only inside a longer one needs Calvin (F5)", "scan hit (1)" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "68703834+calvindotsg@users.noreply.github.com, noreply@anthropic.com\n"})
        check("noreply addresses do not", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({}, {"b.md": "noreply@corp.sg\n"})
        check("noreply@ at any other domain needs Calvin (F9)", "scan hit (1)" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "noreply@anthropic.com.evil.sg\n"})
        check("...and a look-alike of a safe noreply domain", "scan hit (1)" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "call +65 9123 4567\n"})
        check("a new phone number needs Calvin", "scan hit" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "chat -1001234567890\n"})
        check("a new chat id needs Calvin", "scan hit" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "-----BEGIN RSA PRIVATE KEY-----\n"})
        check("a private key header needs Calvin", "scan hit" in last(r.run(repo, b)))
        repo, b = r.repo({"b.md": "token=" + FAKE_SECRET + "\nwrite jane@corp.sg\n"}, {"b.md": "ok\n"})
        check("only added lines are scanned (removing a secret is no hit)",
              last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({"a.txt": "token=" + FAKE_SECRET + "\n"}, {"b.txt": "token=" + FAKE_SECRET + "\n"})
        check("a secret the base already holds still needs Calvin", "scan hit" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "ann@host.test, bob@x.invalid, eve@example.org\n"})
        check(".test, .invalid and example addresses do not", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({}, {"b.md": "jane@example.com.evil.sg\n"})
        check("a look-alike example domain needs Calvin", "scan hit (1)" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "bob@users.noreply.github.com.evil.io\n"})
        check("...and a look-alike noreply domain", "scan hit (1)" in last(r.run(repo, b)))
        repo, b = r.repo({"c.txt": "1\nold\n3\n"}, {"c.txt": "1\nnew\ntoken=" + FAKE_SECRET + "\n3\n"})
        p = r.run(repo, b)
        check("a hit is named by its new-file line number", "c.txt:3 github token" in p.stdout, p.stdout)
        repo, b = r.repo({}, {"to-jane@corp.sg.md": "ok\n"})
        check("a file's header lines are not scanned as added text",
              last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({}, {"d.txt": "++ x\ntoken=" + FAKE_SECRET + "\n"})
        check("a content line like a file header hides nothing", "d.txt:2 github token" in r.run(repo, b).stdout)
        repo, b = r.repo({}, {"\u00e9 q.txt": "token=" + FAKE_SECRET + "\n"})
        p = r.run(repo, b)
        check("a path git quotes is still scanned", "scan hit (1)" in last(p), p.stdout)
        repo, b = r.repo({}, {"f.txt": "a\x0ctoken=" + FAKE_SECRET + "\n"})
        check("a form feed in a line hides nothing", "scan hit (1)" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"nul.bin": "x\0\ntoken=" + FAKE_SECRET + "\n"})
        p = r.run(repo, b)
        check("a file git calls binary is still scanned", "nul.bin:2 github token" in p.stdout, p.stdout + p.stderr)
        repo, b = r.repo({}, {".gitattributes": "*.txt binary\n", "hid.txt": "token=" + FAKE_SECRET + "\n"})
        p = r.run(repo, b)
        check("...and so is one the PR's .gitattributes calls binary", "hid.txt:1 github token" in p.stdout,
              p.stdout + p.stderr)

        print("-- codeowners (plan 3.4 file paths; miss 2026-11-20-merged-past-3.4-01) --")
        co = ("# plan 3.4\n.github/ @calvindotsg\n/gate @calvindotsg\nscripts/gate* @calvindotsg\nscripts/gate/ @calvindotsg\n"
              "skills/orchestrator/scripts/test_*.sh @calvindotsg\nsettings.json @calvindotsg\nBrewfile @calvindotsg\n")
        orch = {".github/CODEOWNERS": co, "skills/orchestrator/scripts/test_orch.sh": "a\n", "docs/a.md": "a\n",
                "gate": "g\n", "scripts/gate/gate-rules.py": "r\n"}
        repo, b = r.repo(orch, {"skills/orchestrator/scripts/test_orch.sh": "a\nb\n"})
        p = r.run(repo, b)
        check("RED: a change to a test runner the gate calls needs Calvin (the miss)",
              last(p) == "needs-calvin=codeowners: 1 path(s) (skills/orchestrator/scripts/test_orch.sh) "
                         "review=skipped-local", p.stdout + p.stderr)
        check("...listed under the rule with its CODEOWNERS file",
              "rule codeowners: 1 changed path(s) owned by @calvindotsg in .github/CODEOWNERS at HEAD and "
              ".github/CODEOWNERS at base\n  skills/orchestrator/scripts/test_orch.sh" in p.stdout, p.stdout)
        p = r.run(repo, b, ci=True, head="headsha")
        check("...and in CI fails until he approves the head (exit 1)", p.returncode == 1 and "review=missing" in last(p),
              p.stdout + p.stderr)
        repo, b = r.repo(orch, {"docs/a.md": "a\nb\n"})
        check("GREEN: a docs-only change needs no one", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo(orch, {"gate": "g2\n", "scripts/gate/gate-rules.py": "r2\n", "scripts/gate_lock.sh": "l\n"})
        check("the gate, the rules copy and a new scripts/gate* file all count",
              "codeowners: 3 path(s)" in last(r.run(repo, b)))
        repo, b = r.repo(orch, {"x/gate": "g\n"})
        check("an anchored /gate does not match x/gate", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo(orch, {"sub/Brewfile": "b\n"})
        check("a pattern with no slash matches at any depth", "codeowners: 1 path(s) (sub/Brewfile)" in last(r.run(repo, b)))
        repo, b = r.repo(orch, {".github/CODEOWNERS": "docs/ @calvindotsg\n",
                                "skills/orchestrator/scripts/test_orch.sh": "a\nb\n"})
        check("a PR cannot drop its own path: the base's CODEOWNERS still holds it",
              "skills/orchestrator/scripts/test_orch.sh" in last(r.run(repo, b)))
        repo, b = r.repo({"a.txt": "a\n", "gate": "g\n"}, {"CODEOWNERS": "/gate @calvindotsg\n", "gate": "g2\n"})
        check("the PR that adds CODEOWNERS (at the root) is held by it", "codeowners: 1 path(s) (gate)" in last(r.run(repo, b)))
        repo, b = r.repo(orch, {}, delete=("gate",))
        p = r.run(repo, b)
        check("deleting an owned file counts too", "codeowners: 1 path(s) (gate)" in last(p) and "deletes 1" in last(p),
              last(p))
        repo, b = r.repo(orch, {}, move=(("gate", "old-gate"),))
        check("a rename away from an owned path counts", "codeowners: 1 path(s) (gate)" in last(r.run(repo, b)))
        repo, b = r.repo({"CODEOWNERS": "* @calvindotsg\ndocs/\n", "docs/a.md": "a\n", "b.md": "b\n"},
                         {"docs/a.md": "a\nb\n"})
        check("the last matching line decides, and one with no owner leaves a path unowned",
              last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({"CODEOWNERS": "* @someone-else\n", "a.md": "a\n"}, {"a.md": "b\n"})
        check("a path owned only by someone else is not his", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({"CODEOWNERS": "*.md @CalvinDotSG\n", "a.md": "a\n"}, {"a.md": "b\n"})
        check("owners match case-insensitively", "codeowners: 1 path(s)" in last(r.run(repo, b)))
        repo, b = r.repo({"CODEOWNERS": "docs/* @calvindotsg\n", "docs/a/b.md": "a\n"}, {"docs/a/b.md": "b\n"})
        check("* stays inside one segment: docs/* is not docs/a/b.md",
              last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({"CODEOWNERS": "docs/** @calvindotsg\n", "docs/a/b.md": "a\n"}, {"docs/a/b.md": "b\n"})
        check("** crosses segments", "codeowners: 1 path(s)" in last(r.run(repo, b)))
        repo, b = r.repo({"a.txt": "a\n"}, {"a.txt": "b\n"})
        check("no CODEOWNERS file: the rule says so and fires nothing",
              "rule codeowners: 0 changed path(s) owned by @calvindotsg in no CODEOWNERS file" in r.run(repo, b).stdout)

        print("-- config safety (Calvin 25 Nov, record 5315bcee, option 2) --")
        def cfg(deny=("Bash(rm -rf *)", "Bash(sudo *)"), hooks=(("PreToolUse", "Bash", "bash $HOME/.claude/scripts/rm-guard.sh"),
                ("Stop", "", "python3 -I $HOME/.claude/skills/o/scripts/tail_guard.py")), status="python3 $HOME/.claude/scripts/statusline.py"):
            h = {}
            for e, m, c in hooks:
                h.setdefault(e, []).append({"matcher": m, "hooks": [{"type": "command", "command": c}]})
            return json.dumps({"permissions": {"deny": list(deny), "allow": []}, "hooks": h,
                               "statusLine": {"type": "command", "command": status}}, indent=2) + "\n"
        home = {"settings.json": cfg(), "scripts/rm-guard.sh": "r\n", "scripts/test_rm_guard.sh": "t\n",
                "scripts/test_other.sh": "o\n", "scripts/statusline.py": "s\n", "scripts/test_statusline.sh": "t\n",
                "skills/o/scripts/tail_guard.py": "t\n", "skills/o/scripts/test_orch.sh": "t\n",
                ".gitignore": "/tmp/\n!/settings.json\n/skills/synced/\n", "gate": 'check "a" x\ncheck "b" y\n',
                "scripts/gate/gate-rules.py": "r\n", "scripts/gate/gate-rules.sha256": "abc  gate-rules.py\n",
                ".github/workflows/gate.yml": "on: pull_request\n", "notes.md": "n\n"}
        repo, b = r.repo(home, {"settings.json": cfg(deny=("Bash(sudo *)",))})
        p = r.run(repo, b)
        check("RED settings: a lost deny rule needs Calvin",
              "settings.json weakened (settings.json deny rule lost: Bash(rm -rf *))" in last(p), p.stdout)
        p = r.run(repo, b, ci=True, head="headsha")
        check("...and in CI fails until he approves (exit 1)", p.returncode == 1 and "review=missing" in last(p), p.stdout)
        repo, b = r.repo(home, {"settings.json": cfg(hooks=(("Stop", "", "python3 -I $HOME/.claude/skills/o/scripts/tail_guard.py"),))})
        check("RED settings: a lost hook", "hook lost or changed: PreToolUse 'Bash'" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"settings.json": cfg(hooks=(("PreToolUse", "Bash", "true"),
                                                            ("Stop", "", "python3 -I $HOME/.claude/skills/o/scripts/tail_guard.py")))})
        check("RED settings: a hook command changed", "hook lost or changed: PreToolUse 'Bash'" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"settings.json": cfg(status="true")})
        check("RED settings: the statusLine command changed", "statusLine command changed" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"settings.json": "{not json\n"})
        check("RED settings: a settings.json that no longer parses", "no longer Claude Code settings" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"settings.json": cfg(deny=("Bash(rm -rf *)", "Bash(sudo *)", "Bash(curl *)"),
                                                     hooks=(("PreToolUse", "Bash", "bash $HOME/.claude/scripts/rm-guard.sh"),
                                                            ("Stop", "", "python3 -I $HOME/.claude/skills/o/scripts/tail_guard.py"),
                                                            ("Stop", "", "bash $HOME/.claude/scripts/new.sh")))})
        check("GREEN settings: adding a deny rule and a hook needs no one",
              last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo({"zed/settings.json": '{"theme": "a"}\n'}, {"zed/settings.json": '{"theme": "b"}\n'})
        check("GREEN settings: a settings.json that is not Claude Code's is not read",
              last(r.run(repo, b)) == "needs-calvin=none review=not-needed")

        repo, b = r.repo(home, {".gitignore": "/tmp/\n!/settings.json\n/skills/synced/\n!/.credentials.json\n"})
        check("RED gitignore: un-ignoring a secret path", ".gitignore un-ignores (.gitignore un-ignores !/.credentials.json)"
              in last(r.run(repo, b)))
        repo, b = r.repo(home, {".gitignore": "/tmp/\n!/settings.json\n/skills/synced/\n!/sessions/\n"})
        check("RED gitignore: ... a sessions folder", ".gitignore un-ignores" in last(r.run(repo, b)))
        repo, b = r.repo(home, {".gitignore": "/tmp/\n!/settings.json\n/skills/synced/\n!/cmux/**\n"})
        check("RED gitignore: opening a whole tree", "un-ignores !/cmux/**" in last(r.run(repo, b)))
        repo, b = r.repo(home, {".gitignore": "/tmp/\n!/settings.json\n"})
        check("RED gitignore: dropping an ignore line", "drops the ignore /skills/synced/" in last(r.run(repo, b)))
        repo, b = r.repo(home, {".gitignore": "/tmp/\n!/settings.json\n/skills/synced/\n# plans\n!/plans/x.md\n"})
        check("GREEN gitignore: whitelisting one plain file, with a comment",
              last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo(home |{".gitignore": "# why\n/tmp/\n!/settings.json\n/skills/synced/\n"},
                         {".gitignore": "/tmp/\n!/settings.json\n/skills/synced/\n"})
        check("GREEN gitignore: removing a comment line", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo(home, {".gitignore": "/tmp/\n/skills/synced/\n"})
        check("GREEN gitignore: removing a whitelist line ignores more", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")

        repo, b = r.repo(home, {"scripts/rm-guard.sh": "r2\n"})
        check("RED guards: a hook guard changed without its test",
              "hook guard changed without its test (scripts/rm-guard.sh (its test: scripts/test_rm_guard.sh))" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"scripts/rm-guard.sh": "r2\n", "scripts/test_other.sh": "o2\n"})
        check("RED guards: another suite changing is not its test", "scripts/rm-guard.sh" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"scripts/rm-guard.sh": "r2\n", "scripts/test_rm_guard.sh": "t2\n"})
        check("GREEN guards: the guard with its test", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo(home, {"scripts/statusline.py": "s2\n"})
        check("RED guards: the statusLine script counts", "scripts/statusline.py" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"skills/o/scripts/tail_guard.py": "t2\n"})
        check("RED guards: no test named for it, none beside it changed", "tail_guard.py" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"skills/o/scripts/tail_guard.py": "t2\n", "skills/o/scripts/test_orch.sh": "t2\n"})
        check("GREEN guards: ...and with the suite beside it", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo(home, {"settings.json": cfg(hooks=(("PreToolUse", "Bash", "bash $HOME/.claude/scripts/rm-guard.sh"),
                                                            ("Stop", "", "python3 -I $HOME/.claude/skills/o/scripts/tail_guard.py"),
                                                            ("Stop", "", "bash $HOME/.claude/scripts/new.sh"))),
                                "scripts/new.sh": "n\n"})
        check("RED guards: a new hook script needs a test too", "scripts/new.sh" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"notes.md": "n2\n"})
        check("GREEN: a change to no config path needs no one", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")

        repo, b = r.repo(home, {"gate": 'check "a" x\ncheck "b" y2\n'})
        check("RED gatefile: a changed check line", "gate line removed or changed (gate: check \"b\" y)" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"gate": 'check "a" x\n'})
        check("RED gatefile: a removed check line", "gate line removed or changed" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"gate": 'check "a" x\ncheck "b" y\ncheck "c" z\n'})
        check("GREEN gatefile: a new check line", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        repo, b = r.repo(home, {"gate": 'check "a" x\n', ".gitattributes": "gate -diff\n"})
        check("RED gatefile: a .gitattributes that marks the gate binary hides nothing",
              "gate line removed or changed (gate: check \"b\" y)" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"gate": 'check "a" x\n', ".gitattributes": "gate diff=fake\n"})
        subprocess.run(["git", "-C", str(repo), "config", "diff.fake.textconv", "true"], check=True)
        check("RED gatefile: a textconv driver hides nothing", "gate line removed or changed" in last(r.run(repo, b)))
        repo, b = r.repo(home, {".gitignore": "/tmp/\n!/settings.json\n", ".gitattributes": ".gitignore -diff\n"})
        check("RED gitignore: a .gitattributes that marks it binary hides nothing",
              "drops the ignore /skills/synced/" in last(r.run(repo, b)))
        repo, b = r.repo(home, {".gitignore": "/tmp/\n!/settings.json\n", ".gitattributes": ".gitignore diff=fake\n"})
        subprocess.run(["git", "-C", str(repo), "config", "diff.fake.textconv", "true"], check=True)
        check("RED gitignore: a textconv driver hides nothing", "drops the ignore /skills/synced/" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"x/gate": "g\n"})
        check("GREEN gatefile: only the root gate counts", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")

        repo, b = r.repo(home, {"scripts/gate/gate-rules.py": "r2\n"})
        check("RED rulesfile: the vendored rules", "gate rules or pin changed (scripts/gate/gate-rules.py)" in last(r.run(repo, b)))
        repo, b = r.repo(home, {"scripts/gate/gate-rules.sha256": "def  gate-rules.py\n"})
        check("RED rulesfile: the pin alone", "gate rules or pin changed (scripts/gate/gate-rules.sha256)" in last(r.run(repo, b)))
        repo, b = r.repo(home, {".github/workflows/gate.yml": "on: push\n"})
        check("RED workflows: a changed workflow", "CI workflow changed (.github/workflows/gate.yml)" in last(r.run(repo, b)))
        repo, b = r.repo(home, {".github/workflows/new.yml": "on: push\n"})
        check("RED workflows: a new workflow", "CI workflow changed (.github/workflows/new.yml)" in last(r.run(repo, b)))
        repo, b = r.repo(home, {".github/PULL_REQUEST_TEMPLATE.md": "t\n"})
        check("GREEN workflows: other .github files", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
        p = r.run(*r.repo(home, {"notes.md": "n3\n"}))
        check("every config-safety rule prints its line",
              all(f"rule {n}: 0 hit(s)" in p.stdout for n in ("settings", "gitignore", "guards", "gatefile", "rulesfile",
                                                               "workflows")), p.stdout)

        print("-- the trusted judge (Q5 a, record 919abb82) --")
        judge = {**home, "scripts/gate/gate-rules.py": tool.read_text(),
                 "scripts/gate/gate-rules.sha256": "pin  gate-rules.py\n",
                 ".github/CODEOWNERS": "/scripts/owned.sh @calvindotsg\n", "scripts/owned.sh": "o\n"}
        TJ = {"GATE_TRUSTED_JUDGE": "1"}

        def jrun(repo, b, overlay=(), **kw):
            """Run the repo's own rules file, after putting the base's copy of `overlay` paths over the tree."""
            if overlay:
                subprocess.run(["git", "-C", str(repo), "checkout", b, "--", *overlay], check=True, capture_output=True)
            return r.run(repo, b, tool=repo / "scripts/gate/gate-rules.py", **kw)

        JUDGE = ("gate", "scripts/gate", ".github/CODEOWNERS")
        repo, b = r.repo(judge, {"notes.md": "n2\n"})
        p = jrun(repo, b, **TJ)
        check("TJ: nothing fires, the tag says none",
              last(p) == "needs-calvin=none review=not-needed judge=none" and p.returncode == 0
              and "gate-rules: trusted judge: the base's judge runs" in p.stdout, p.stdout)
        repo, b = r.repo(judge, {"gate": 'check "a" x\ncheck "b" y2\n'})
        p = jrun(repo, b, overlay=JUDGE, ci=True, **TJ)
        check("TJ gatefile: a tag, not Calvin; CI passes with no review read",
              last(p) == 'needs-calvin=none review=not-needed judge=gate line removed or changed (gate: check "b" y)'
              and p.returncode == 0 and not Api.calls, p.stdout + p.stderr)
        repo, b = r.repo(judge, {"scripts/gate/gate-rules.py": tool.read_text() + "# new\n",
                                 "scripts/gate/gate-rules.sha256": "pin2  gate-rules.py\n"})
        p = jrun(repo, b, overlay=JUDGE, ci=True, **TJ)
        check("TJ rulesfile: the rules and the pin are a tag",
              last(p).startswith("needs-calvin=none review=not-needed judge=gate rules or pin changed (")
              and p.returncode == 0, p.stdout + p.stderr)
        repo, b = r.repo(judge, {".github/workflows/gate.yml": "on: push\n"})
        p = jrun(repo, b, ci=True, **TJ)
        check("TJ workflows: a tag", last(p) == "needs-calvin=none review=not-needed judge=CI workflow changed "
              "(.github/workflows/gate.yml)" and p.returncode == 0, p.stdout + p.stderr)
        repo, b = r.repo(judge, {"scripts/owned.sh": "o2\n"})
        p = jrun(repo, b, ci=True, **TJ)
        check("TJ codeowners: a tag", last(p) == "needs-calvin=none review=not-needed judge=codeowners: 1 path(s) "
              "(scripts/owned.sh)" and p.returncode == 0, p.stdout + p.stderr)
        repo, b = r.repo(judge, {".github/CODEOWNERS": "/scripts/owned.sh @someone\n", "scripts/owned.sh": "o2\n"})
        p = jrun(repo, b, overlay=JUDGE, **TJ)
        check("TJ: a PR that drops its own path from CODEOWNERS is still tagged, judged by the base's copy",
              "judge=codeowners: 1 path(s) (scripts/owned.sh)" in last(p)
              and "trusted judge refused" not in p.stdout, p.stdout)
        repo, b = r.repo(judge, {"settings.json": cfg(deny=("Bash(sudo *)",))})
        p = jrun(repo, b, ci=True, head="headsha", **TJ)
        check("TJ settings: a lost deny rule still needs Calvin (exit 1)",
              last(p) == "needs-calvin=settings.json weakened (settings.json deny rule lost: Bash(rm -rf *)) "
              "review=missing judge=none" and p.returncode == 1, p.stdout + p.stderr)
        repo, b = r.repo(judge, {".gitignore": "/tmp/\n!/settings.json\n"})
        check("TJ gitignore: still needs Calvin", "needs-calvin=.gitignore un-ignores" in last(jrun(repo, b, **TJ)))
        repo, b = r.repo(judge, {"scripts/rm-guard.sh": "r2\n"})
        check("TJ guards: still needs Calvin", "needs-calvin=hook guard changed" in last(jrun(repo, b, **TJ)))
        repo, b = r.repo(judge, {}, delete=("notes.md",))
        check("TJ deleted: still needs Calvin", "needs-calvin=deletes 1 tracked file(s)" in last(jrun(repo, b, **TJ)))
        repo, b = r.repo(judge, {"settings.json": cfg(deny=("Bash(sudo *)",)), ".github/workflows/gate.yml": "on: push\n"})
        p = jrun(repo, b, ci=True, head="headsha", **TJ)
        check("TJ mixed: Calvin for settings, a tag for the workflow, CI waits on him",
              last(p).startswith("needs-calvin=settings.json weakened") and last(p).endswith(
                  " review=missing judge=CI workflow changed (.github/workflows/gate.yml)") and p.returncode == 1,
              p.stdout)
        p = jrun(repo, b, ci=True, head="headsha", GITHUB_EVENT_NAME="schedule", **TJ)
        check("TJ mixed outside a PR event: cannot decide (exit 2), tag kept",
              p.returncode == 2 and "judge=CI workflow changed" in p.stderr, p.stdout + p.stderr)

        def refused(change, path, overlay=(), label=""):
            repo, b = r.repo(judge, change)
            p = jrun(repo, b, overlay=overlay, ci=True, head="headsha", **TJ)
            check(f"TJ refused: {label or path} not the base's copy, so today's rules (exit 1)",
                  f"trusted judge refused: {path} is not the base's copy" in p.stdout and " judge=" not in last(p)
                  and "review=missing" in last(p) and p.returncode == 1, p.stdout + p.stderr)
        refused({"gate": 'check "a" x\nGATE_TRUSTED_JUDGE=1 check "b" y\n'}, "gate", label="a PR's own gate")
        refused({"scripts/gate/gate-rules.py": tool.read_text().replace('"rulesfile", "workflows", "codeowners")',
                                                                         '"rulesfile", "workflows", "codeowners", "settings")', 1)
                 + "\n"}, "scripts/gate/gate-rules.py", label="a PR's own rules")
        refused({"scripts/gate/gate-rules.sha256": "pin2  gate-rules.py\n", "scripts/owned.sh": "o2\n"},
                "scripts/gate/gate-rules.sha256", label="the pin")
        repo, b = r.repo(judge, {"scripts/owned.sh": "o2\n"})
        (repo / "scripts/gate/gate-rules.sha256").unlink()
        p = jrun(repo, b, ci=True, head="headsha", **TJ)
        check("TJ refused: the pin gone from the tree", "trusted judge refused: scripts/gate/gate-rules.sha256 is not "
              "the base's copy" in p.stdout and p.returncode == 1, p.stdout + p.stderr)
        refused({".github/CODEOWNERS": "/scripts/other.sh @calvindotsg\n", "gate": 'check "a" x\n'},
                ".github/CODEOWNERS", overlay=("gate",))
        repo, b = r.repo({k: v for k, v in judge.items() if k != "gate"}, {"gate": "check x\n", "scripts/owned.sh": "o2\n"})
        p = jrun(repo, b, ci=True, head="headsha", **TJ)
        check("TJ refused: a gate the base never had", "trusted judge refused: gate is not the base's copy" in p.stdout
              and p.returncode == 1, p.stdout)
        repo, b = r.repo(judge, {"scripts/gate/fnmatch.py": "print('PLANTED')\n", "scripts/owned.sh": "o2\n"})
        p = jrun(repo, b, overlay=JUDGE, ci=True, head="headsha", **TJ)
        check("TJ refused: a module the PR added beside the rules (Python would import it)",
              "trusted judge refused: scripts/gate/fnmatch.py is not the base's copy" in p.stdout
              and p.returncode == 1, p.stdout + p.stderr)
        p = jrun(repo, b, ci=True, head="headsha")
        check("a module beside the rules never runs (its folder is off the import path)",
              "PLANTED" not in p.stdout + p.stderr and "review=missing" in last(p), p.stdout + p.stderr)
        repo, b = r.repo(judge, {"scripts/owned.sh": "o2\n"})
        (repo / "scripts/gate/json").mkdir()
        (repo / "scripts/gate/json/__init__.py").write_text("print('PLANTED')\n")
        (repo / "scripts/gate/__pycache__").mkdir()
        (repo / "scripts/gate/__pycache__/x.pyc").write_bytes(b"c")
        p = jrun(repo, b, ci=True, head="headsha", **TJ)
        check("TJ refused: an untracked package beside the rules",
              "trusted judge refused: scripts/gate/json/__init__.py is not the base's copy" in p.stdout
              and p.returncode == 1, p.stdout + p.stderr)
        shutil.rmtree(repo / "scripts/gate/json")
        os.symlink("/tmp", repo / "scripts/gate/lnk")
        p = jrun(repo, b, ci=True, head="headsha", **TJ)
        check("TJ refused: a symlinked folder beside the rules", "trusted judge refused: scripts/gate/lnk is not the "
              "base's copy" in p.stdout and p.returncode == 1, p.stdout + p.stderr)
        (repo / "scripts/gate/lnk").unlink()
        p = jrun(repo, b, ci=True, head="headsha", **TJ)
        check("TJ: a __pycache__ beside the rules is not counted", "judge=codeowners: 1 path(s)" in last(p)
              and p.returncode == 0, p.stdout + p.stderr)
        os.chmod(repo / "gate", 0)
        p = jrun(repo, b, ci=True, head="headsha", **TJ)
        os.chmod(repo / "gate", 0o644)
        check("TJ refused: a comparison that fails is a refusal, not trust",
              "trusted judge refused: cannot compare the judge with the base" in p.stdout and " judge=" not in last(p)
              and p.returncode == 1, p.stdout + p.stderr)
        reset_api()
        repo2, b2 = r.repo(judge, {"settings.json": cfg(deny=("Bash(sudo *)",)), "scripts/owned.sh": "o2\n"})
        Api.status = 500
        p = jrun(repo2, b2, ci=True, head="headsha", **TJ)
        reset_api()
        check("TJ: a review that cannot be read keeps the tag (exit 2)", p.returncode == 2
              and "review=undecided judge=codeowners: 1 path(s)" in p.stderr, p.stdout + p.stderr)
        repo, b = r.repo(judge, {".github/workflows/a\nneeds-calvin=none review=not-needed": "on: push\n"})
        p = jrun(repo, b, **TJ)
        check("TJ: a newline in a path stays on the tag's line",
              last(p).startswith("needs-calvin=none review=not-needed judge=CI workflow changed (.github/workflows/a\\x0a")
              and [l for l in p.stdout.splitlines() if l.startswith("needs-calvin=")][-1] == last(p)
              and last(p).endswith(")"), p.stdout)
        repo, b = r.repo(judge, {".github/workflows/a\nneeds-calvin=none review=not-needed": "on: push\n"})
        p = r.run(repo, b)
        check("a newline in a path never ends the reason as none", last(p).startswith(
              "needs-calvin=CI workflow changed (.github/workflows/a\\x0aneeds-calvin=none"), p.stdout)
        repo, b = r.repo(judge, {"scripts/owned.sh": "o2\n"})
        (repo / "pin-copy").write_text((repo / "scripts/gate/gate-rules.sha256").read_text())
        (repo / "scripts/gate/gate-rules.sha256").unlink()
        os.symlink(repo / "pin-copy", repo / "scripts/gate/gate-rules.sha256")
        p = jrun(repo, b, ci=True, head="headsha", **TJ)
        check("TJ refused: a judge file swapped for a symlink, same bytes behind it",
              "trusted judge refused: scripts/gate/gate-rules.sha256 is not the base's copy" in p.stdout
              and p.returncode == 1, p.stdout + p.stderr)
        mark = r.dir / "mark"
        mark.mkdir(exist_ok=True)
        (mark / "fnmatch.py").write_text(
            "print('KEPT')\nimport importlib.util, sysconfig\n"
            "_s = importlib.util.spec_from_file_location('_f', sysconfig.get_paths()['stdlib'] + '/fnmatch.py')\n"
            "_m = importlib.util.module_from_spec(_s)\n_s.loader.exec_module(_m)\nfnmatchcase = _m.fnmatchcase\n")
        p = subprocess.run([sys.executable, "-I", "-c", "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
                            "sys.argv = ['gate-rules.py', '--base', sys.argv[3]]; "
                            "runpy.run_path(sys.argv and __import__('os').environ['T'], run_name='__main__')",
                            str(mark), "x", b], cwd=repo, env={"PATH": "/usr/bin:/bin", "T": str(tool)},
                           capture_output=True, text=True, timeout=60)
        check("an isolated run (-I) keeps its import path whole", "KEPT" in p.stdout and "needs-calvin=" in p.stdout,
              p.stdout + p.stderr)
        p = r.run(repo, b, ci=True, head="headsha", **TJ)
        check("TJ refused: a rules file outside the repository",
              "trusted judge refused: this rules file is outside the repository" in p.stdout and p.returncode == 1,
              p.stdout)
        p = jrun(repo, b, ci=True, head="headsha", GATE_TRUSTED_JUDGE="true")
        check("TJ: only the value 1 asks; anything else is today's output",
              last(p) == "needs-calvin=codeowners: 1 path(s) (scripts/owned.sh) review=missing"
              and "trusted judge" not in p.stdout and p.returncode == 1, p.stdout)
        p = jrun(repo, b)
        check("TJ unset: no tag, no trusted-judge line",
              last(p) == "needs-calvin=codeowners: 1 path(s) (scripts/owned.sh) review=skipped-local"
              and "trusted judge" not in p.stdout, p.stdout)

        print("-- bytes that are not UTF-8 (F7) --")
        repo, b = r.repo({"a.txt": "a\n"}, {})
        (repo / "font.woff2").write_bytes(b"wOF2\x88\xff\x00\x9c\ntoken=" + FAKE_SECRET.encode() + b"\n")
        commit_all(repo)
        p = r.run(repo, b)
        check("a font's non-UTF-8 bytes neither crash the run nor hide a secret",
              p.returncode == 0 and "font.woff2:2 github token" in p.stdout, p.stdout + p.stderr)
        repo, b = r.repo({"a.txt": "a\n"}, {})
        odd = b"caf\xe9-\x88.txt"   # latin-1, not UTF-8: git keeps such a name as bytes
        blob = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "--stdin"], capture_output=True, check=True,
                              input=b"token=" + FAKE_SECRET.encode() + b"\n").stdout.strip()
        subprocess.run([b"git", b"-C", str(repo).encode(), b"update-index", b"--add", b"--cacheinfo",
                        b"100644," + blob + b"," + odd], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "odd name"], check=True, capture_output=True)
        p = r.run(repo, b, PYTHONIOENCODING="utf-8:strict")   # stdout as in CI (LANG=C.UTF-8), not the C locale's escapes
        check("a file whose name is not UTF-8 is still scanned, and its name printed escaped",
              p.returncode == 0 and "github token" in p.stdout and "scan hit (1)" in last(p) and "Traceback" not in p.stderr,
              p.stdout + p.stderr)

        print("-- the review, in CI --")
        repo, b = r.repo({"a.txt": "a\n", "b.txt": "b\n"}, {}, delete=("b.txt",))
        cal = {"login": "calvindotsg"}
        reset_api()
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
        check("no token fails closed (exit 2)", p.returncode == 2 and "GATE_GITHUB_TOKEN" in p.stderr, p.stderr)
        check("...and calls nothing", not Api.calls, str(Api.calls))
        p = r.run(repo, b, ci=True, head="H2", author="calvindotsg")
        check("a PR Calvin opened himself: exit 1, review=self-authored, with the way out",
              p.returncode == 1 and "review=self-authored" in last(p) and "re-open it via gh-bot" in p.stdout,
              p.stdout + p.stderr)
        check("...decided without reading reviews", not Api.calls, str(Api.calls))
        for label, ev in (("an event that is a list", []), ("a null pull_request", {"pull_request": None}),
                          ("an event with no author", {"pull_request": {"number": 7, "head": {"sha": "H2"}}})):
            p = r.run(repo, b, ci=True, event=ev)
            check(f"{label} fails closed (exit 2)", p.returncode == 2 and "cannot read the review" in p.stderr,
                  p.stdout + p.stderr)
        reset_api()
        Api.body = {"message": "not a list"}
        p = r.run(repo, b, ci=True, head="H2")
        check("a reviews body that is not a list fails closed (exit 2)", p.returncode == 2, p.stdout + p.stderr)
        reset_api()
        Api.link, Api.reviews2 = r.api() + "/page2", [{"user": cal, "state": "APPROVED", "commit_id": "H2"}]
        p = r.run(repo, b, ci=True, head="H2")
        check("the approval on page 2 is read (exit 0)", p.returncode == 0 and "review=approved" in last(p),
              p.stdout + p.stderr)
        Api.link = r.elsewhere() + "/page2"
        p = r.run(repo, b, ci=True, head="H2")
        check("a next page on another origin is not fetched (exit 2, one call)",
              p.returncode == 2 and len(Api.calls) == 1, p.stdout + p.stderr + str(Api.calls))
        Api.link = "https://api.github.com.evil.example/page2"
        p = r.run(repo, b, ci=True, head="H2")
        check("...and on a look-alike host", p.returncode == 2 and len(Api.calls) == 1, str(Api.calls))
        reset_api()
        Api.redirect = r.elsewhere() + "/elsewhere"
        p = r.run(repo, b, ci=True, head="H2")
        check("a redirect is not followed with the token (exit 2)", p.returncode == 2 and len(Api.calls) == 1,
              p.stdout + p.stderr)
        reset_api()
        for ev_name in ("push", "merge_group", "workflow_dispatch"):
            p = r.run(repo, b, ci=True, head="H2", GITHUB_EVENT_NAME=ev_name)
            check(f"a rule firing on a {ev_name} run fails closed (exit 2)",
                  p.returncode == 2 and "review=undecided" in p.stderr and not Api.calls, p.stdout + p.stderr)
        repo2, b2 = r.repo({"a.txt": "a\n"}, {"a.txt": "a\nb\n"})
        p = r.run(repo2, b2, ci=True, GITHUB_EVENT_NAME="push")
        check("...while a push with nothing to flag passes", p.returncode == 0, p.stdout + p.stderr)
        p = r.run(repo, b, args=("--frobnicate",))
        check("an unknown argument fails closed (exit 2)", p.returncode == 2, p.stderr)

        print("-- the default base (F12) --")
        local, pr, f = merges(r)
        p = r.run(local, None)
        check("a local head that merged main is measured from the merge-base, not its old tip",
              p.returncode == 0 and "rule deleted: 0" in p.stdout and last(p) == "needs-calvin=none review=not-needed",
              p.stdout + p.stderr)
        p = r.run(local, None, GITHUB_EVENT_NAME="pull_request")
        check("...and a PR event name outside Actions changes nothing", p.returncode == 0 and "rule deleted: 0" in p.stdout,
              p.stdout + p.stderr)
        p = r.run(local, None, ci=True, head="H", GITHUB_EVENT_NAME="push")
        check("...nor a non-PR event in Actions: merge-base", p.returncode == 0 and "rule deleted: 0" in p.stdout,
              p.stdout + p.stderr)
        for ev_name in ("pull_request", "pull_request_review"):
            p = r.run(pr, None, ci=True, head=f, GITHUB_EVENT_NAME=ev_name)
            check(f"GitHub's {ev_name} merge of the event's head is measured from HEAD^1",
                  p.returncode == 0 and "rule deleted: 0" in p.stdout and "changed outside memory" in p.stdout,
                  p.stdout + p.stderr)
        p = r.run(pr, None, ci=True, head="0" * 40)
        check("a merge of some other head fails closed (exit 2, pass --base)",
              p.returncode == 2 and "pass --base" in p.stderr and "rule " not in p.stdout, p.stdout + p.stderr)
        p = r.run(local, None, ci=True, head=f)
        check("a PR event whose HEAD is a merged-main branch head fails closed (exit 2)",
              p.returncode == 2 and "pass --base" in p.stderr, p.stdout + p.stderr)
        repo, b = r.repo({"a.txt": "a\n"}, {"a.txt": "a\nb\n"})
        p = r.run(repo, None, ci=True, head="H")
        check("a PR event on a one-parent HEAD fails closed (exit 2)",
              p.returncode == 2 and "pass --base" in p.stderr, p.stdout + p.stderr)
        p = r.run(pr, None, ci=True, event={"pull_request": {"number": 7}})
        check("an event with no head sha fails closed (exit 2)",
              p.returncode == 2 and "pass --base" in p.stderr, p.stdout + p.stderr)
        p = r.run(pr, None, ci=True, head=f, GITHUB_EVENT_PATH=str(r.dir / "no-such-event.json"))
        check("an event file that cannot be read fails closed (exit 2)",
              p.returncode == 2 and "pass --base" in p.stderr, p.stdout + p.stderr)
        p = r.run(local, "main", ci=True, head="H")
        check("an explicit --base is used as given, in CI too", p.returncode == 0 and "rule deleted: 0" in p.stdout,
              p.stdout + p.stderr)

        print("-- the base --")
        p = subprocess.run([sys.executable, str(tool), "--base", "no-such-rev"], cwd=repo, capture_output=True,
                           text=True, env={"PATH": "/usr/bin:/bin"})
        check("a bad base fails closed (exit 2)", p.returncode == 2 and "undecided" in p.stderr, p.stderr)
    finally:
        r.close()


MUTATIONS = [
    ("binaries not listed", "            binaries.append(f", "            pass  # "),
    ("binary size wrong", "'cat-file', '-s'", "'cat-file', '-t'"),
    ("deletes ignored", "    if deleted:\n        hits.append", "    if False:\n        hits.append"),
    ("line limit off by one", "    if lines > MAX_LINES:", "    if lines >= MAX_LINES:"),
    ("memory counted in lines", "            mem += 1\n            continue\n", "            mem += 1\n"),
    ("memory limit off by one", "    if mem > MAX_MEMORY:", "    if mem >= MAX_MEMORY:"),
    ("secret scan off", "                if rx.search(text):\n                    found.append",
     "                if False:\n                    found.append"),
    ("identifiers in the base not excused", "                    if not in_base(base, rx, value):",
     "                    if True:"),
    ("removed lines scanned too", "            if not hunk or not line.startswith(\"+\"):",
     "            if not hunk or line.startswith(\"@\"):"),
    ("a stale approval accepted", "    return \"approved\" if mine[-1].get(\"commit_id\") == head else \"stale\"",
     "    return \"approved\""),
    ("first review, not latest, decides", "mine[-1][\"state\"] != \"APPROVED\"", "mine[0][\"state\"] != \"APPROVED\""),
    ("any reviewer counts", "            if (r.get(\"user\") or {}).get(\"login\") == approver\n            and ",
     "            if "),
    ("API failure passes", "            return 2\n    print(f\"needs-calvin={reasons} review={state}{tag}\")",
     "            return 0\n    print(f\"needs-calvin={reasons} review={state}{tag}\")"),
    ("stale exits 0", "(\"missing\", \"stale\", \"self-authored\") else 0", "(\"missing\", \"self-authored\") else 0"),
    ("the secret value printed", "                    found.append(f\"{path}:{n} {kind}\")",
     "                    found.append(f\"{path}:{n} {kind} {text}\")"),
    ("renames detected", "\"-z\", \"--no-renames\", \"--diff-filter=D\"", "\"-z\", \"--find-renames\", \"--diff-filter=D\""),
    ("secrets excused like identifiers", "                if rx.search(text):\n                    found",
     "                if rx.search(text) and not in_base(base, rx.search(text).group(0)):\n                    found"),
    ("safe emails widened to every .test-less address", "\\.(test|invalid|example))$", "\\.(test|invalid|example|sg))$"),
    ("safe domains unanchored", "|\\.(test|invalid|example))$\", re.I)", "|\\.(test|invalid|example)$)\", re.I)"),
    ("line numbers count removed lines", "            if not hunk or not line.startswith(\"+\"):\n                continue",
     "            if not hunk or not line.startswith(\"+\"):\n                n += line.startswith(\"-\")\n                continue"),
    ("scan reads the header lines", "            if not hunk or not line.startswith(\"+\"):",
     "            if not line.startswith(\"+\"):"),
    ("scan splits on every line break", "base, \"HEAD\", \"--\", path).split(\"\\n\"):",
     "base, \"HEAD\", \"--\", path).splitlines():"),
    ("no token check", "    if not token:\n        raise", "    if False:\n        raise"),
    ("look-alike API host honoured", "if origin(API) != (\"https\", \"api.github.com\") and not (",
     "if \"api.github.com\" not in API and not ("),
    ("next page anywhere", "            if m and origin(m.group(1)) != origin(API):", "            if False:"),
    ("redirects followed", "OPENER = urllib.request.build_opener(NoRedirect)", "OPENER = urllib.request.build_opener()"),
    ("self-authored not detected", "    if pr[\"user\"][\"login\"] == approver:", "    if False:"),
    ("self-authored passes", "    return 1 if state in (\"missing\", \"stale\", \"self-authored\") else 0",
     "    return 1 if state in (\"missing\", \"stale\") else 0"),
    ("a bad event passes as missing", "TypeError, AttributeError, ", ""),
    ("binary files skipped", "\"-U0\", \"--text\", ", "\"-U0\", "),
    ("other CI events pass as local", "    elif event not in (\"pull_request\", \"pull_request_review\"):",
     "    elif False:"),
    ("noreply@ at any domain (F9)", "(^noreply@(anthropic\\.com|github\\.com)|", "(^noreply@|"),
    ("base match by substring (F5)", "    return p.returncode == 0 and any(", "    return p.returncode == 0 or any("),
    ("unknown argument ignored","            return 2\n    try:", "            i += 1\n            continue\n    try:"),
    ("any 2-parent HEAD is the PR merge (F12)", "        parents = git(\"rev-list\", \"--parents\", \"-n\", \"1\", \"HEAD\").split()\n        if len",
     "        pass\n    parents = git(\"rev-list\", \"--parents\", \"-n\", \"1\", \"HEAD\").split()\n    if len(parents) == 3:\n        return parents[1]\n    if True:\n        if len"),
    ("HEAD^1 outside Actions (F12)", "    if os.environ.get(\"GITHUB_ACTIONS\") == \"true\" and os.environ.get(\"GITHUB_EVENT_NAME\")",
     "    if os.environ.get(\"GITHUB_EVENT_NAME\")"),
    ("HEAD^1 under any CI event (F12)", "    if os.environ.get(\"GITHUB_ACTIONS\") == \"true\" and os.environ.get(\"GITHUB_EVENT_NAME\") in (\n            \"pull_request\", \"pull_request_review\"):",
     "    if os.environ.get(\"GITHUB_ACTIONS\") == \"true\":"),
    ("review events not read as PR merges (F12)", "in (\n            \"pull_request\", \"pull_request_review\"):", "in (\n            \"pull_request\",):"),
    ("HEAD^2 not checked (F12)", "if len(parents) == 3 and parents[2] == head:", "if len(parents) == 3:"),
    ("git output decoded strictly (F7)", "encoding=\"utf-8\", errors=\"surrogateescape\")", "encoding=\"utf-8\")"),
    ("odd bytes replaced, not carried (F7)", "encoding=\"utf-8\", errors=\"surrogateescape\")",
     "encoding=\"utf-8\", errors=\"replace\")"),
    ("odd path printed strictly (F7)", "    sys.stdout.reconfigure(errors=\"backslashreplace\")", "    pass"),
    ("codeowners never fires", "    if owned:\n", "    if False:\n"),
    ("codeowners: any owner counts", "            if last_owners and me in last_owners:", "            if last_owners:"),
    ("codeowners: head file only (a PR drops its own path)", "for rev in (base, \"HEAD\")]", "for rev in (\"HEAD\",)]"),
    ("codeowners: base file only (the adding PR passes)", "for rev in (base, \"HEAD\")]", "for rev in (base,)]"),
    ("codeowners: first match wins", "                    last_owners = owners\n",
     "                    last_owners = owners if last_owners is None else last_owners\n"),
    ("codeowners: never anchored", "    anchored = \"/\" in pat.rstrip(\"/\")", "    anchored = False"),
    ("codeowners: * crosses segments", "            out, i = out + \"[^/]*\", i + 1", "            out, i = out + \".*\", i + 1"),
    ("codeowners: deletions skipped", "    for path in git(\"diff\", \"--name-only\", \"-z\", \"--no-renames\", base, \"HEAD\").split(\"\\0\"):\n        if not path:\n            continue\n        for _, rows",
     "    for path in git(\"diff\", \"--name-only\", \"-z\", \"--no-renames\", \"--diff-filter=d\", base, \"HEAD\").split(\"\\0\"):\n        if not path:\n            continue\n        for _, rows"),
    ("settings: deny loss ignored", "settings += [f\"{path} deny rule lost: {d}\" for d in sorted(before[0] - after[0])]",
     "pass"),
    ("settings: hook loss ignored", "settings += [f\"{path} hook lost or changed: {e} {m!r}\" for e, m, _ in sorted(before[1] - after[1])]",
     "pass"),
    ("settings: statusLine ignored", "        if before[2] and before[2] != after[2]:", "        if False:"),
    ("settings: unparseable head passes", "        if not isinstance(after, tuple):\n", "        if after is None:\n"),
    ("settings: never fires", "            hits.append((name, f\"{reason}", "            0 and hits.append((name, f\"{reason}"),
    ("gitignore: secret words off", "(SECRET_WORDS.search(rule) or rule.endswith", "(False or rule.endswith"),
    ("gitignore: whole tree allowed", "or rule.endswith((\"/**\", \"/*\")))", "or False)"),
    ("gitignore: dropped ignores allowed", "            elif line[0] == \"-\" and not rule.startswith(\"!\"):", "            elif False:"),
    ("gitignore: comments count", "            if not rule or rule.startswith(\"#\"):", "            if not rule:"),
    ("gitignore: removing a whitelist line fires", "            elif line[0] == \"-\" and not rule.startswith(\"!\"):",
     "            elif line[0] == \"-\":"),
    ("guards: any test beside counts", "(named or [p for p in head_files + changed if here(p)])",
     "([p for p in head_files + changed if here(p)])"),
    ("guards: statusLine not read", "[c for _, _, c in side[1]] + [side[2]]", "[c for _, _, c in side[1]]"),
    ("guards: base settings only", "        for side in (before, after):\n", "        for side in (before,):\n"),
    ("gatefile: additions fire", "            if line.startswith(\"-\") and not line.startswith(\"---\"):",
     "            if line[:1] in \"+-\" and not line.startswith((\"---\", \"+++\")):"),
    ("gatefile: never fires", "    if \"gate\" in changed:", "    if False:"),
    ("rulesfile: pin not counted", "in (\"gate-rules.py\", \"gate-rules.sha256\")]", "in (\"gate-rules.py\",)]"),
    ("workflows: never fires", "flows = [p for p in changed if p.startswith(\".github/workflows/\")]", "flows = []"),
    ("workflows: all of .github", "flows = [p for p in changed if p.startswith(\".github/workflows/\")]",
     "flows = [p for p in changed if p.startswith(\".github/\")]"),
    ("TJ: settings judged as a tag", "JUDGE_RULES = (\"gatefile\", \"rulesfile\", \"workflows\", \"codeowners\")",
     "JUDGE_RULES = (\"gatefile\", \"rulesfile\", \"workflows\", \"codeowners\", \"settings\")"),
    ("TJ: codeowners not a tag", "JUDGE_RULES = (\"gatefile\", \"rulesfile\", \"workflows\", \"codeowners\")",
     "JUDGE_RULES = (\"gatefile\", \"rulesfile\", \"workflows\")"),
    ("TJ: judge never compared", "        if now != blob(base, path):", "        if False:"),
    ("TJ: pin not compared", "    paths = [os.path.join(folder, \"gate-rules.sha256\"), \"gate\", *CODEOWNERS_FILES]",
     "    paths = [\"gate\", *CODEOWNERS_FILES]"),
    ("TJ: gate not compared", "    paths = [os.path.join(folder, \"gate-rules.sha256\"), \"gate\", *CODEOWNERS_FILES]",
     "    paths = [os.path.join(folder, \"gate-rules.sha256\"), *CODEOWNERS_FILES]"),
    ("TJ: CODEOWNERS not compared", "    paths = [os.path.join(folder, \"gate-rules.sha256\"), \"gate\", *CODEOWNERS_FILES]",
     "    paths = [os.path.join(folder, \"gate-rules.sha256\"), \"gate\"]"),
    ("TJ: a missing file on one side passes", "        if now != blob(base, path):",
     "        if now is not None and blob(base, path) is not None and now != blob(base, path):"),
    ("TJ: any value asks", "    if os.environ.get(\"GATE_TRUSTED_JUDGE\") != \"1\":", "    if not os.environ.get(\"GATE_TRUSTED_JUDGE\"):"),
    ("TJ: outside the repository allowed", "    if rel.startswith(\"..\" + os.sep) or rel == \"..\":", "    if False:"),
    ("TJ: tags still need Calvin", "    hits = [h for h in hits if h not in judged]\n", "    hits = hits\n"),
    ("TJ: no tag printed", "    tag = f\" judge={", "    tag = \"\" and f\" judge={"),
    ("TJ: a failed comparison trusted", "        trusted, refused = False, f\"cannot compare", "        trusted, refused = True, \"\" or f\"cannot compare"),
    ("TJ: no tag when the review cannot be read", "review=undecided{tag}\\ngate-rules: cannot read", "review=undecided\\ngate-rules: cannot read"),
    ("TJ: no tag outside a PR event", "review=undecided{tag}\\ngate-rules: a {event}", "review=undecided\\ngate-rules: a {event}"),
    ("TJ: an empty tag for none", "judged)) or 'none'}\"", "judged)) or ''}\""),
    ("TJ: no trusted-judge line", "        print(\"gate-rules: trusted judge: the base's", "        0 and print(\"gate-rules: trusted judge: the base's"),
    ("TJ: files beside the rules not compared", "        paths += [os.path.relpath(os.path.join(root, f), top) for f in sorted(files)]\n", ""),
    ("TJ: symlinked folders beside the rules not compared", "        paths += [os.path.relpath(os.path.join(root, d), top) for d in dirs if os.path.islink(os.path.join(root, d))]\n", ""),
    ("TJ: __pycache__ counted", "        dirs[:] = sorted(d for d in dirs if d != \"__pycache__\")", "        dirs[:] = sorted(dirs)"),
    ("control characters printed raw", "    return re.sub(r\"[\\x00-\\x1f\\x7f]\"", "    return re.sub(r\"[\\x00]\""),
    ("the rules file's folder left on the import path", "    del sys.path[0]\n", "    pass\n"),
    ("TJ: a symlinked judge file hashed through", "if os.path.isfile(here) and not os.path.islink(here) else", "if os.path.isfile(here) else"),
    ("an isolated run loses its first path entry", "sys.path and not sys.flags.isolated and not", "sys.path and not"),
    ("gatefile: binary diff", "\"--text\", \"--no-textconv\", \"--no-color\", \"--no-ext-diff\",\n                        base, \"HEAD\", \"--\", \"gate\")",
     "\"--no-color\", \"--no-ext-diff\",\n                        base, \"HEAD\", \"--\", \"gate\")"),
    ("gatefile: textconv honoured", "\"--text\", \"--no-textconv\", \"--no-color\", \"--no-ext-diff\",\n                        base, \"HEAD\", \"--\", \"gate\")",
     "\"--text\", \"--no-color\", \"--no-ext-diff\",\n                        base, \"HEAD\", \"--\", \"gate\")"),
    ("gitignore: binary diff", "\"--text\", \"--no-textconv\", \"--no-color\", \"--no-ext-diff\",\n                        base, \"HEAD\", \"--\",\n",
     "\"--no-color\", \"--no-ext-diff\",\n                        base, \"HEAD\", \"--\",\n"),
    ("gitignore: textconv honoured", "\"--text\", \"--no-textconv\", \"--no-color\", \"--no-ext-diff\",\n                        base, \"HEAD\", \"--\",\n",
     "\"--text\", \"--no-color\", \"--no-ext-diff\",\n                        base, \"HEAD\", \"--\",\n"),
    ("TJ: refusal silent", "        print(f\"gate-rules: trusted judge refused: {refused}", "        0 and print(f\"gate-rules: trusted judge refused: {refused}"),
    ("a non-merge guessed from the merge-base (F12)","        raise RuntimeError(\"HEAD is not GitHub's merge of this PR's head: pass --base\")\n", ""),
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
