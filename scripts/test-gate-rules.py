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
            (r / p).write_text(text)
        g("add", "-A")
        g("commit", "-qm", "base", "--allow-empty")
        b = g("rev-parse", "HEAD").stdout.strip()
        for p, text in change.items():
            (r / p).parent.mkdir(parents=True, exist_ok=True)
            (r / p).write_text(text)
        for p in delete:
            (r / p).unlink()
        for old, new in move:
            g("mv", old, new)
        g("add", "-A")
        g("commit", "-qm", "change", "--allow-empty")
        return r, b

    def run(self, repo: Path, base: str, ci: bool = False, head: str = "", author: str = "calvindotsg-agent[bot]",
            event=None, args: tuple = (), **extra: str):
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
        return subprocess.run([sys.executable, str(self.tool), "--base", base, *args], cwd=repo, env=env,
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
        repo, b = r.repo({"a.txt": "token=" + FAKE_SECRET + "\n"}, {"b.txt": "token=" + FAKE_SECRET + "\n"})
        check("a secret the base already holds still needs Calvin", "scan hit" in last(r.run(repo, b)))
        repo, b = r.repo({}, {"b.md": "ann@host.test, bob@x.invalid, eve@example.org\n"})
        check(".test, .invalid and example addresses do not", last(r.run(repo, b)) == "needs-calvin=none review=not-needed")
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
    ("secret scan off", "                if rx.search(text):\n                    found.append",
     "                if False:\n                    found.append"),
    ("identifiers in the base not excused", "                    if not in_base(base, value):",
     "                    if True:"),
    ("removed lines scanned too", "            if not hunk or not line.startswith(\"+\"):",
     "            if not hunk or line.startswith(\"@\"):"),
    ("a stale approval accepted", "    return \"approved\" if mine[-1].get(\"commit_id\") == head else \"stale\"",
     "    return \"approved\""),
    ("first review, not latest, decides", "mine[-1][\"state\"] != \"APPROVED\"", "mine[0][\"state\"] != \"APPROVED\""),
    ("any reviewer counts", "            if (r.get(\"user\") or {}).get(\"login\") == approver\n            and ",
     "            if "),
    ("API failure passes", "            return 2\n    print(f\"needs-calvin={reasons} review={state}\")",
     "            return 0\n    print(f\"needs-calvin={reasons} review={state}\")"),
    ("stale exits 0", "(\"missing\", \"stale\", \"self-authored\") else 0", "(\"missing\", \"self-authored\") else 0"),
    ("the secret value printed", "                    found.append(f\"{path}:{n} {kind}\")",
     "                    found.append(f\"{path}:{n} {kind} {text}\")"),
    ("renames detected", "\"-z\", \"--no-renames\", \"--diff-filter=D\"", "\"-z\", \"--find-renames\", \"--diff-filter=D\""),
    ("secrets excused like identifiers", "                if rx.search(text):\n                    found",
     "                if rx.search(text) and not in_base(base, rx.search(text).group(0)):\n                    found"),
    ("safe emails widened to every .test-less address", "\\.(test|invalid|example)$", "\\.(test|invalid|example|sg)$"),
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
    ("unknown argument ignored", "            return 2\n    try:", "            i += 1\n            continue\n    try:"),
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
