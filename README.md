# .github

Default [community health files](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file) for `calvindotsg` repositories.

## What This Provides

Files in this repository are automatically inherited by all `calvindotsg` repos that don't define their own versions:

| File | Purpose |
|------|---------|
| `SECURITY.md` | Security policy — report via GitHub Private Vulnerability Reporting, or email where that is unavailable |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR checklist — conventional commits, tests, linter |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | Generic bug report form |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | Generic feature request form |

## Override Behavior

- **PR template**: A repo's own `.github/PULL_REQUEST_TEMPLATE.md` takes precedence.
- **Issue templates**: Override is **directory-level** — if a repo has its own `.github/ISSUE_TEMPLATE/` directory, ALL templates from this repo are ignored. Include a complete set of templates in the override.
- **SECURITY.md**: A repo's own `SECURITY.md` (root or `.github/`) takes precedence.

## Setup Script

`scripts/setup-repo.sh` applies a shared baseline to **one repository at a time**. It is opt-in: nothing in this repo applies it automatically, and it has not been run against every `calvindotsg` repo. Treat the list below as what the script *does*, not as a description of any given repo's current state.

- Squash-only merges with PR title + description
- Auto-delete branches on merge, auto-merge enabled
- Wiki and Projects disabled
- Dependabot alerts and security updates
- Private vulnerability reporting
- Branch protection (PRs required, enforce admins, no force push) — status check names are set separately, since they vary per CI matrix

It also **reports** — writing nothing — on whether Dependabot *version* updates are actually
live. That one is a report rather than a setting because GitHub gives it no API: alerts and
security updates have endpoints, version updates are enabled solely by a `dependabot.yml`
existing, and nothing tells you whether it is running. The failure it catches is real and
silent: a fork inherits a valid config at fork time and GitHub withholds version updates from
it until someone clicks Enable, so the file looks configured, opens nothing, and the pins go
stale anyway. `calvindotsg/portfolio-v2` sat like that for 17 days.

```bash
./scripts/setup-repo.sh calvindotsg/<repo-name>
```

Project-specific settings (homepage, topics, status check names) are set separately — the script prints a reminder.

To check whether a repo has actually had the baseline applied, query **both** protection
systems. Neither endpoint reports the other, so checking one alone is wrong in both directions.

```bash
REPO=calvindotsg/<repo-name>

# `gh api` exits non-zero for BOTH "not configured" (404) and "the call failed", and it prints
# its error body to STDOUT — so neither the exit status nor the output is a safe test on its
# own. Read the HTTP status instead, so a rate limit or a 5xx is never reported as "not set".
code() { gh api "$1" -i --silent 2>/dev/null | head -1 | awk '{print $2}'; }
say() {
  case "$2" in
    200|204) echo "$1: yes" ;;
    404)     echo "$1: no" ;;
    '')      echo "$1: UNKNOWN — no response" ;;
    *)       echo "$1: UNKNOWN — HTTP $2" ;;
  esac
}

# 1. Classic branch protection.
say "classic protection" "$(code "repos/${REPO}/branches/main/protection")"

# 2. Rulesets. Step 1 does not report these, and this does not report classic protection.
RULES=$(gh api "repos/${REPO}/rules/branches/main" --jq '[.[].type]' 2>/dev/null) \
  || RULES="UNKNOWN — lookup failed"
echo "rulesets: ${RULES}"

# 3. The security half of the baseline — branch protection implies none of it.
say "dependabot alerts" "$(code "repos/${REPO}/vulnerability-alerts")"
PVR=$(gh api "repos/${REPO}/private-vulnerability-reporting" --jq '.enabled' 2>/dev/null) \
  || PVR="UNKNOWN — endpoint is public-and-not-archived only"
echo "private vulnerability reporting: ${PVR}"
```

`main` is protected if step 1 says `yes` **or** step 2 returns a non-empty list.

- A `404` from step 1 alone means only that *classic* protection is absent. `granola-to-minutes`
  returns 404 there while carrying a ruleset that requires a `test` check and permits no bypass —
  stricter than the script's own baseline, and invisible to a check that only looks at step 1.
- The reverse also holds: `cc-menubar` returns 200 with three required checks. Branch protection
  is not a proxy for the security settings, so step 3 is not optional.

## Related

- **Template repos** (private): scaffold new projects with boilerplate code
- **This repo**: provides community files + settings script
- **Workflow**: create from template → run setup script → set project-specific values
