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
- Default workflow permissions set to `read`. Whether Actions may **create and approve** pull requests is preserved rather than overwritten, and `ACTIONS_MAY_OPEN_PRS=yes|no` sets it explicitly — one GitHub setting covers both verbs, so forcing it off breaks any workflow that opens its own pull request. That is not hypothetical: it broke `portfolio-v2`'s nightly for two nights. The better fix for such a repo is a GitHub App or PAT for that one call, which this setting does not govern
- Branch protection (PRs required, enforce admins, no force push) — status check names are set separately, since they vary per CI matrix, and any already configured are preserved on re-run

Public repositories only, because GitHub offers these nowhere else:

- Private vulnerability reporting
- Secret scanning and push protection. Note this covers *provider* patterns — tokens whose shape
  GitHub and its partners publish. Private keys, connection strings and bespoke secrets need
  `secret_scanning_non_provider_patterns`, which is a paid feature and is off across this account.

The script reads the repository's visibility and archived state before writing anything. An
archived repo is read-only, so it stops and says so instead of failing on the first write; a
private repo has the two public-only steps skipped rather than aborting on them. If that metadata
cannot be read at all, it writes nothing.

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

# 0. Can this token see the repo at all? Without this gate every 404 below is ambiguous
#    between "the setting is off" and "wrong name, or wrong `gh` account" — and a typo would
#    otherwise be reported as a confident set of "no"s about a repo never reached.
if [ "$(code "repos/${REPO}")" != "200" ]; then
  echo "cannot read ${REPO} — check the name and 'gh auth status'"
else

  # 1. Classic branch protection.
  say "classic protection" "$(code "repos/${REPO}/branches/main/protection")"

  # 2. Rulesets. Step 1 does not report these, and this does not report classic protection.
  #    Note it lists only ACTIVE rulesets, so an empty list is not proof none exist.
  if [ "$(code "repos/${REPO}/rules/branches/main")" = "200" ]; then
    echo "rulesets: $(gh api "repos/${REPO}/rules/branches/main" --jq '[.[].type]')"
  else
    echo "rulesets: UNKNOWN — lookup failed"
  fi

  # 3. The security half of the baseline — branch protection implies none of it.
  say "dependabot alerts" "$(code "repos/${REPO}/vulnerability-alerts")"

  # PVR needs the body, not just the status: a 200 can still say enabled=false. A 422 is the
  # documented "must be public and not archived", which is a fact about the repo, not a failure.
  case "$(code "repos/${REPO}/private-vulnerability-reporting")" in
    200) echo "private vuln reporting: $(gh api "repos/${REPO}/private-vulnerability-reporting" --jq '.enabled')" ;;
    422) echo "private vuln reporting: n/a — repo is private or archived" ;;
    *)   echo "private vuln reporting: UNKNOWN" ;;
  esac
fi
```

`main` is protected if step 1 says `yes` **or** step 2 returns a non-empty list.

- A `404` from step 1 alone means only that *classic* protection is absent. `granola-to-minutes`
  returns 404 there while carrying a ruleset that requires a `test` check and permits no bypass —
  stricter than the script's own baseline, and invisible to a check that only looks at step 1.
- The reverse also holds: `cc-menubar` returns 200 with three required checks. Branch protection
  is not a proxy for the security settings, so step 3 is not optional.

## Social Preview Cards

`social-preview/` holds a card template and `scripts/social-preview.sh`, which draws a
repository's social preview and uploads it. GitHub has no API for that setting — description,
homepage and topics are all writable over REST, and the one field deciding what a link looks like
in Slack or on LinkedIn is not — so the script drives the Settings page's own upload requests
from a signed-in browser pane.

```bash
./scripts/social-preview.sh --render-only calvindotsg/mac-upkeep   # draw it, change nothing
./scripts/social-preview.sh calvindotsg/mac-upkeep                 # draw, upload, verify
./scripts/social-preview.sh --remove calvindotsg/mac-upkeep        # back to GitHub's default
```

All six public repositories get a card. GitHub's generated card carries four live counters —
contributors, issues, stars, forks — which is the usual reason not to replace it; across these
repositories those counters total 5 stars, 1 fork and 0 issues, and four of six read zero
throughout, so a quarter of the generated card is an empty scoreboard. The card here keeps what
was worth keeping (avatar, and the proportional language bar, reused as the divider between prose
and metadata), drops the empty counters, and adds a licence, topics and — for the three
repositories that have one — an **install command**, which no generated card can ever show.
`repos.json` holds those commands and any per-repository theme override.

**Nothing is uploaded yet.** GitHub's repository-image pipeline has been broken since 2026-08-21:
the upload succeeds and `og:image` is repointed, but the bytes never reach the CDN, so a
configured repository unfurls as a *broken* image. Verified across four uploads on `portfolio-v2`
and reverted. See [social-preview/README.md](social-preview/README.md) for the comparison that
sets the bar, the upload protocol, and how to check whether GitHub has fixed it.

**One card is in use regardless.** `homebrew-tap`'s README opens with its own, hotlinked from
`social-preview/images/` here rather than copied into that repository, so regenerating a card
updates every README showing it instead of leaving a second copy to drift. `images/dark/` holds
the same six drawn with `--theme dark`, because a README is rendered in the *reader's* theme and
a light-canvas PNG glares on GitHub dark mode; the pair is embedded as `<picture>`. None of that
depends on the broken pipeline above — the social preview slot takes one image and it is the
light one. A card needs the **full column width** to stay readable, which rules out a
two-column gallery; `social-preview/README.md` has the measurements.

Requires macOS with cmux running, since the upload needs a real authenticated browser session.

## Related

- **Template repos** (private): scaffold new projects with boilerplate code
- **This repo**: provides community files + settings script
- **Workflow**: create from template → run setup script → set project-specific values
