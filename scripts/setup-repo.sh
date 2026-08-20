#!/usr/bin/env bash
set -euo pipefail

# --- Abort reporting ---
# The writes below land in sequence, so an abort part-way through leaves the repository in a
# mixed state — auto-merge and branch deletion on, branch protection absent — with nothing to
# say so. The most reachable trigger is the documented workflow itself: a repo created from a
# template with no first commit has no `main`, so the branch-protection call 404s. Private repos
# hit the same shape earlier, at the public-only private-vulnerability-reporting PUT.
#
# ${LINENO} in an ERR trap reports the LAST line of a multi-line command — for the
# branch-protection PUT that is the heredoc terminator, not the `gh api` line — so it is not
# enough on its own. STEP names the operation; `step` sets it and prints the same progress line
# the script printed before.
STEP="startup"
step() {
  STEP="$1"
  echo "  $1..."
}
trap 'echo "" >&2
      echo "!! ABORTED during: ${STEP} (near line ${LINENO})" >&2
      echo "   ${REPO:-The repository} is PARTIALLY configured: writes before this point were" >&2
      echo "   applied, later ones were not. Fix the cause and re-run — re-running is safe." >&2' ERR

# Shared repository settings for calvindotsg projects.
# Idempotent — safe to re-run. All commands use PUT/PATCH semantics, and the branch-protection
# PUT reads the existing required status checks back before replacing the object, so a re-run
# preserves them instead of clearing them.
#
# Usage:
#   ./setup-repo.sh OWNER/REPO
#
# Project-specific settings NOT included (set separately):
#   - Homepage URL (PyPI vs npm vs none)
#   - Topics (vary per project)
#   - Branch protection status check names (vary per CI matrix)

REPO="${1:?Usage: $0 OWNER/REPO}"

# --- Manual-steps checklist ---
# Printed from an EXIT trap so it appears on the failure path too. A partial run needs this
# checklist more than a clean one does: the abort message above says what was skipped, this says
# what was never automated in the first place. Registered after REPO is known so a usage error
# does not print it.
print_manual_steps() {
  echo ""
  echo "==> Manual steps remaining:"
  echo "  1. Set homepage URL:  gh repo edit ${REPO} --homepage <url>"
  echo "  2. Set topics:        gh repo edit ${REPO} --add-topic <topic>"
  echo "  3. Set status checks: gh api --method PUT repos/${REPO}/branches/main/protection --input <payload>"
  echo "  4. Sidebar (About gear icon): uncheck Deployments and Packages if not used"
}
trap print_manual_steps EXIT

echo "==> Configuring ${REPO}"

# --- Merge strategy ---
# Squash-only keeps main history clean. PR title + description become the commit
# message, which pairs well with conventional commits and release-please.
step "Setting merge strategy (squash-only)"
gh repo edit "${REPO}" \
  --enable-squash-merge \
  --enable-merge-commit=false \
  --enable-rebase-merge=false \
  --squash-merge-commit-message pr-title-description

# --- Repository features ---
# delete-branch-on-merge: prevents stale branch accumulation after PR merge.
# auto-merge: lets release-please PRs merge once CI passes without manual action.
# allow-update-branch: shows "Update branch" button on PRs when behind main.
# wiki/projects: unused — disable to reduce surface area.
step "Setting repository features"
gh repo edit "${REPO}" \
  --delete-branch-on-merge \
  --enable-auto-merge \
  --allow-update-branch \
  --enable-wiki=false \
  --enable-projects=false

# --- Security ---
# Dependabot alerts + security updates for dependency vulnerabilities.
# Private vulnerability reporting so users can report issues without public disclosure.
step "Enabling security features"
gh api --method PUT "repos/${REPO}/vulnerability-alerts" --silent
gh api --method PUT "repos/${REPO}/automated-security-fixes" --silent
gh api --method PUT "repos/${REPO}/private-vulnerability-reporting" --silent

# --- Branch protection ---
# Require PRs (0 approvals — solo maintainer). On its own this does NOT enforce CI: with no
# contexts in the payload there is nothing to gate on, which is why any that already exist are
# read back and written straight through rather than overwritten.
# Enforce admins so the maintainer can't bypass accidentally.
# No force pushes or deletions on main.
#
# NOTE: Status check names are NOT set here because they vary per project
# (e.g. "test (3.11)" for Python vs "test" for TypeScript).
# Set them separately:
#   gh api --method PUT repos/OWNER/REPO/branches/main/protection \
#     --input <payload-with-contexts>
# Once set, re-running this script keeps them — see the read-back below.
#
# This endpoint reports and replaces CLASSIC protection only. A branch protected by a ruleset is
# invisible to it and is left untouched; see README.md for how to check both systems.
step "Setting branch protection (preserving any status checks already set)"

# PUT here is FULL REPLACEMENT, and required_status_checks is a REQUIRED field — it cannot be
# omitted, and null means "disable" rather than "leave alone". Sending null therefore deletes the
# contexts the note above tells the operator to add: the script would silently undo its own
# documented follow-up step on every re-run. So read them back first and write them through.
# A 404 is the ordinary case (no checks configured, or no protection at all yet) and yields null.
EXISTING_CHECKS=$(gh api "repos/${REPO}/branches/main/protection/required_status_checks" \
  --jq '{strict, contexts}' 2>/dev/null) || EXISTING_CHECKS="null"
[ -n "${EXISTING_CHECKS}" ] || EXISTING_CHECKS="null"

# Heredoc below is UNQUOTED on purpose so ${EXISTING_CHECKS} expands. Nothing else in the payload
# contains a $ or a backtick, so nothing else expands.
gh api --method PUT "repos/${REPO}/branches/main/protection" \
  --silent \
  --input - <<EOF
{
  "required_status_checks": ${EXISTING_CHECKS},
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF

# --- Dependabot liveness (REPORT ONLY — this section writes nothing) ---
#
# The three PUTs above cover ALERTS and SECURITY updates, which have API endpoints. They say
# nothing about VERSION updates, which have none: version updates are enabled solely by a
# `dependabot.yml` being present, and GitHub exposes no endpoint that reports whether they are
# actually running. So this is the one part of the script that has to be inferred rather than
# set, and it is why it reports instead of acting.
#
# IT EXISTS BECAUSE THE SILENT FAILURE IS REAL, not hypothetical. `calvindotsg/portfolio-v2`
# carried a valid `dependabot.yml` for 17 days that did nothing, because the repository was a
# fork: GitHub withholds version updates from a fork whose config arrived that way, and asks
# for a separate click under Settings. Nothing surfaces that. The file looks configured, the
# pins go stale anyway, and the only tell is a bump that never arrives.
echo ""
echo "==> Dependabot version updates (report only, nothing written):"

# NOTE ON THE GUARD: test the EXIT STATUS, never the output. `gh api` prints its 404 JSON body
# to STDOUT, so `[ -n "$(gh api ... 2>/dev/null)" ]` is true for every repository and silently
# reports that they all have the file. That mistake produced a wrong census once already.
if gh api "repos/${REPO}/contents/.github/dependabot.yml" --silent >/dev/null 2>&1; then
  echo "  config:    .github/dependabot.yml present"

  # The `||` must sit OUTSIDE the substitution. Inside it, the sentinel is appended to gh's error
  # body — which the warning above says goes to stdout — and the result equals neither "true" nor
  # "0" nor "?", so every arm below falls through to the success arm. Capture on success only,
  # then validate against the expected domain so an unexpected 200 is demoted to "?" as well.
  IS_FORK=$(gh api "repos/${REPO}" --jq '.fork' 2>/dev/null) || IS_FORK="?"
  case "${IS_FORK}" in true|false) ;; *) IS_FORK="?" ;; esac

  IS_ARCHIVED=$(gh api "repos/${REPO}" --jq '.archived' 2>/dev/null) || IS_ARCHIVED="?"
  case "${IS_ARCHIVED}" in true|false) ;; *) IS_ARCHIVED="?" ;; esac

  # This dynamic workflow appears once Dependabot has actually run an update job. Treat it as
  # corroboration rather than proof: it is a small sample, and GitHub documents no contract for it.
  HAS_RUNS=$(gh api "repos/${REPO}/actions/workflows" \
    --jq '[.workflows[] | select(.name == "Dependabot Updates")] | length' 2>/dev/null) || HAS_RUNS="?"
  case "${HAS_RUNS}" in ''|*[!0-9]*) HAS_RUNS="?" ;; esac

  # `gh pr list` exits 0 and prints 0 for an unreachable repository, so a zero here cannot be
  # told apart from a genuine absence of bot PRs. It is corroboration, never proof on its own.
  PR_COUNT=$(gh pr list --repo "${REPO}" --state all --author "app/dependabot" \
    --limit 100 --json number --jq 'length' 2>/dev/null) || PR_COUNT="?"
  case "${PR_COUNT}" in ''|*[!0-9]*) PR_COUNT="?" ;; esac

  echo "  fork:      ${IS_FORK}"
  echo "  archived:  ${IS_ARCHIVED}"
  echo "  update runs seen: ${HAS_RUNS}    bot PRs ever: ${PR_COUNT}"

  if [ "${IS_FORK}" = "true" ]; then
    echo "  ⚠  THIS IS A FORK, so version updates are OFF until someone clicks Enable:"
    echo "     Settings > Advanced Security > Dependabot version updates"
  elif [ "${IS_ARCHIVED}" = "true" ]; then
    echo "  ·  Archived — nothing will open a pull request here. Expected, not a fault."
  elif [ "${PR_COUNT}" = "0" ] && [ "${HAS_RUNS}" = "0" ]; then
    echo "  ⚠  No bot pull request has ever been opened and no update run is visible."
    echo "     Either the config is newer than its first interval, or it is not running."
    echo "     Confirm against a dependency whose newer release predates the last expected run."
  elif [ "${IS_FORK}" = "?" ] || [ "${IS_ARCHIVED}" = "?" ] || \
       [ "${HAS_RUNS}" = "?" ] || [ "${PR_COUNT}" = "?" ]; then
    echo "  ?  Could not determine liveness — an API lookup failed. Nothing below it is known."
    echo "     Re-run before trusting this section."
  else
    echo "  ✓  Has produced update activity."
  fi
else
  echo "  config:    no .github/dependabot.yml — version updates are not configured"
fi

# --- Template detection (optional nudge) ---
# Same capture-on-success shape as the liveness lookups above: with the `||` inside, a failed
# lookup would put gh's error body into TEMPLATE_SOURCE and the guard below would print it back
# as a detected template. The case arm rejects anything that is not a bare OWNER/NAME.
TEMPLATE_SOURCE=$(gh api "repos/${REPO}" --jq '.template_repository.full_name // empty' 2>/dev/null) || TEMPLATE_SOURCE=""
case "${TEMPLATE_SOURCE}" in *[!A-Za-z0-9._/-]*) TEMPLATE_SOURCE="" ;; esac
if [ -n "${TEMPLATE_SOURCE}" ]; then
  echo ""
  echo "==> Detected template source: ${TEMPLATE_SOURCE}"
  echo "  If you haven't already, clone this repo and run:"
  echo "    bash scripts/init.sh"
  echo "  to substitute placeholder values."
fi

# Reached only when every write above succeeded; the checklist follows from the EXIT trap.
echo ""
echo "==> Done. ${REPO} configured."
