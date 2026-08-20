#!/usr/bin/env bash
set -euo pipefail

# --- Abort reporting ---
# The writes below land in sequence, so an abort part-way through leaves the repository in a
# mixed state — auto-merge and branch deletion on, branch protection absent — with nothing to
# say so. The most reachable trigger is the documented workflow itself: a repo created from a
# template with no first commit has no `main`, so the branch-protection call 404s. Private repos
# hit the same shape earlier, at the public-only private-vulnerability-reporting PUT.
#
# This fires on a FAILED COMMAND only. Ctrl-C or SIGTERM still leaves a partial configuration
# without this message; the EXIT trap's checklist is what prints then.
#
# ${LINENO} in an ERR trap reports the LAST line of a multi-line command — for the
# branch-protection PUT that is the heredoc terminator, not the `gh api` line — and it must be
# captured on the FIRST line of the trap body, because bash 3.2 offsets it by the reference's own
# position within that body and would otherwise report a blank line. It is a hint either way, so
# STEP names the operation; `step` sets it and prints the same progress line as before.
STEP="startup"
step() {
  STEP="$1"
  echo "  $1..."
}
trap 'ABORT_LINE=${LINENO}
      echo "" >&2
      echo "!! ABORTED during: ${STEP} (near line ${ABORT_LINE})" >&2
      echo "   ${REPO:-The repository} is PARTIALLY configured: writes before this point, if any," >&2
      echo "   were applied, later ones were not. Re-running is safe once the cause is fixed." >&2
      echo "   If this was the private-vulnerability-reporting step on a PRIVATE repo, it is not" >&2
      echo "   fixable: that endpoint is public-only, so the run will stop here every time." >&2' ERR

# Shared repository settings for calvindotsg projects.
# Idempotent — safe to re-run. All commands use PUT/PATCH semantics, and the branch-protection
# PUT reads the existing required status checks back before replacing the object, so a re-run
# keeps their names and the strict flag rather than clearing them.
#
# Two limits on that claim, both deliberate:
#   - It preserves check NAMES, not the `checks` array's app_id bindings. GitHub re-derives those
#     from which app recently reported each context, so they survive in practice but are not
#     written explicitly. Sending `contexts` and `checks` together is undocumented, so this does
#     not do it.
#   - The PUT is full replacement, so every protection field the payload omits reverts to its
#     documented default of false. See the list above the payload.
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
  # Suppressed for a repo that was never eligible for configuration in the first place.
  [ "${SKIP_CHECKLIST:-}" = "1" ] && return 0
  echo ""
  echo "==> Manual steps remaining:"
  echo "  1. Set homepage URL:  gh repo edit ${REPO} --homepage <url>"
  echo "  2. Set topics:        gh repo edit ${REPO} --add-topic <topic>"
  echo "  3. Set status checks: gh api --method PUT repos/${REPO}/branches/main/protection --input <payload>"
  echo "  4. Sidebar (About gear icon): uncheck Deployments and Packages if not used"
}
trap print_manual_steps EXIT

echo "==> Configuring ${REPO}"

# --- Repository shape ---
# Read BEFORE any write. Three of the steps below apply only to some kinds of repository, and
# discovering that by watching a PUT fail means stopping half-configured. Both values are
# captured on success and validated, and an unreadable answer aborts: every branch after this
# point depends on them, so guessing is the one thing that must not happen.
step "Reading repository metadata"
VISIBILITY=$(gh api "repos/${REPO}" --jq '.visibility' 2>/dev/null) || VISIBILITY=""
case "${VISIBILITY}" in public|private|internal) ;; *) VISIBILITY="" ;; esac
IS_ARCHIVED=$(gh api "repos/${REPO}" --jq '.archived' 2>/dev/null) || IS_ARCHIVED=""
case "${IS_ARCHIVED}" in true|false) ;; *) IS_ARCHIVED="" ;; esac
if [ -z "${VISIBILITY}" ] || [ -z "${IS_ARCHIVED}" ]; then
  echo "" >&2
  echo "!! Could not read whether ${REPO} is public or archived, so it is not knowable which" >&2
  echo "   parts of the baseline apply. Nothing has been written. Re-run when the API answers." >&2
  SKIP_CHECKLIST=1
  exit 1
fi
echo "  visibility: ${VISIBILITY}, archived: ${IS_ARCHIVED}"

# An archived repository is read-only: the very first `gh repo edit` below would fail against it.
# Stop cleanly and say so, rather than aborting mid-run with a 403 that reads like a fault.
if [ "${IS_ARCHIVED}" = "true" ]; then
  echo ""
  echo "==> ${REPO} is ARCHIVED, so GitHub holds it read-only and none of the baseline can be"
  echo "    applied. This is expected, not a failure — unarchive it first if it should be live."
  SKIP_CHECKLIST=1
  exit 0
fi

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

# Private vulnerability reporting is offered on PUBLIC repositories only — the endpoint 404s on a
# private repo and 422s on an archived one. Before this check the script aborted here on every
# private repo, which meant branch protection below was never reached on most of the fleet.
if [ "${VISIBILITY}" = "public" ]; then
  gh api --method PUT "repos/${REPO}/private-vulnerability-reporting" --silent
else
  echo "  ·  ${VISIBILITY} repo — private vulnerability reporting is public-only, skipped."
  echo "     SECURITY.md's email fallback is the reporting channel here."
fi

# --- Actions permissions ---
# The GITHUB_TOKEN handed to a workflow that declares no `permissions:` block of its own.
# `write` is GitHub's legacy default and grants contents, issues, pull-requests and packages
# write to every such job — far more than anything here needs, and it is granted to third-party
# actions running in that job too. `read` is the correct floor; a workflow that genuinely needs
# more should say so in its own `permissions:` block, where it is reviewable.
#
# can_approve_pull_request_reviews=false stops Actions approving pull requests, which would
# otherwise let a workflow satisfy a review requirement without a human.
step "Restricting default workflow permissions"
gh api --method PUT "repos/${REPO}/actions/permissions/workflow" \
  --silent \
  -F default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=false

# --- Secret scanning ---
# PUBLIC only, and not by preference: on a private repository both fields require GitHub Advanced
# Security, and without it they are absent from the API response entirely rather than merely
# disabled. Sending them there would fail the run for a feature the account cannot use.
#
# Scope worth knowing: push protection blocks PROVIDER patterns — tokens whose shape GitHub and
# its partners publish. It does not cover private keys, connection strings or bespoke secrets;
# `secret_scanning_non_provider_patterns` is what covers those, it is a paid feature, and it is
# disabled across this fleet. Treat this as a partial control, not a guarantee.
if [ "${VISIBILITY}" = "public" ]; then
  step "Enabling secret scanning and push protection"
  gh api --method PATCH "repos/${REPO}" \
    --silent \
    --input - <<'EOF'
{
  "security_and_analysis": {
    "secret_scanning": { "status": "enabled" },
    "secret_scanning_push_protection": { "status": "enabled" }
  }
}
EOF
else
  echo "  ·  ${VISIBILITY} repo — secret scanning needs GitHub Advanced Security, skipped."
fi

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
#
# FIELDS THIS PAYLOAD OMITS, and which the full-replacement PUT therefore forces to false:
#   required_linear_history, block_creations, required_conversation_resolution, lock_branch,
#   allow_fork_syncing, and the required_pull_request_reviews sub-flags dismiss_stale_reviews,
#   require_code_owner_reviews, require_last_push_approval.
# Every calvindotsg repo with classic protection already has all nine at false, so nothing is
# lost today — but set them in the payload here before relying on any of them.
step "Setting branch protection (preserving any status checks already set)"

# PUT here is FULL REPLACEMENT, and required_status_checks is a REQUIRED field — it cannot be
# omitted, and null means "disable" rather than "leave alone". Sending null therefore deletes the
# contexts the note above tells the operator to add: the script would silently undo its own
# documented follow-up step on every re-run. So read them back first and write them through.
#
# READ THE HTTP STATUS, NOT THE EXIT STATUS. `gh api` exits non-zero for BOTH "no checks
# configured" (404) and "the call failed", so `... || EXISTING_CHECKS="null"` would turn a 403
# secondary rate limit into a confident "nothing to preserve" and PUT null — deleting the very
# contexts this block exists to keep, while still printing "Done." Only 404 is safe to read as
# absence; anything else means we do not know, and not knowing must abort rather than write.
RSC_PATH="repos/${REPO}/branches/main/protection/required_status_checks"
# The `|| true` is INSIDE the substitution here, which is the shape this script warns about
# everywhere else. It is safe in this one place, and only because of both of these: `head -1`
# bounds the capture to the status line, and the `case` below accepts nothing but an exact 200
# or 404. Without it, `set -o pipefail` makes the whole assignment fail on the 404 that this
# block specifically needs to handle as "no checks configured", and the run aborts instead.
RSC_CODE=$(gh api "${RSC_PATH}" -i --silent 2>/dev/null | head -1 | awk '{print $2}' || true)
case "${RSC_CODE}" in
  200)
    EXISTING_CHECKS=$(gh api "${RSC_PATH}" --jq '{strict, contexts}' 2>/dev/null) \
      || EXISTING_CHECKS=""
    ;;
  404)
    # No checks configured, or no protection at all yet. Nothing to preserve.
    EXISTING_CHECKS="null"
    ;;
  *)
    EXISTING_CHECKS=""
    ;;
esac
if [ -z "${EXISTING_CHECKS}" ]; then
  echo "" >&2
  echo "!! Could not read the existing required status checks (HTTP ${RSC_CODE:-no response})." >&2
  echo "   REFUSING to write branch protection: this PUT replaces the whole object, so writing" >&2
  echo "   it now could delete status checks that are configured but unreadable right now." >&2
  echo "   Everything before this step was applied. Re-run once the API is answering." >&2
  exit 1
fi

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
  elif [ "${IS_FORK}" = "?" ] || [ "${IS_ARCHIVED}" = "?" ] || \
       [ "${HAS_RUNS}" = "?" ] || [ "${PR_COUNT}" = "?" ]; then
    # Must outrank the arm below it: with the metadata lookups failed, "0 runs and 0 PRs" is not
    # evidence of anything, and diagnosing two confident causes from it is the failure this
    # section exists to avoid.
    echo "  ?  Could not determine liveness — an API lookup failed. Nothing below it is known."
    echo "     Re-run before trusting this section."
  elif [ "${PR_COUNT}" = "0" ] && [ "${HAS_RUNS}" = "0" ]; then
    echo "  ⚠  No bot pull request has ever been opened and no update run is visible."
    echo "     Either the config is newer than its first interval, or it is not running."
    echo "     Confirm against a dependency whose newer release predates the last expected run."
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
