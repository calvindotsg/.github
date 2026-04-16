#!/usr/bin/env bash
set -euo pipefail

# Shared repository settings for calvindotsg projects.
# Idempotent — safe to re-run. All commands use PUT/PATCH semantics.
#
# Usage:
#   ./setup-repo.sh OWNER/REPO
#
# Project-specific settings NOT included (set separately):
#   - Homepage URL (PyPI vs npm vs none)
#   - Topics (vary per project)
#   - Branch protection status check names (vary per CI matrix)

REPO="${1:?Usage: $0 OWNER/REPO}"

echo "==> Configuring ${REPO}"

# --- Merge strategy ---
# Squash-only keeps main history clean. PR title + description become the commit
# message, which pairs well with conventional commits and release-please.
echo "  Setting merge strategy (squash-only)..."
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
echo "  Setting repository features..."
gh repo edit "${REPO}" \
  --delete-branch-on-merge \
  --enable-auto-merge \
  --allow-update-branch \
  --enable-wiki=false \
  --enable-projects=false

# --- Security ---
# Dependabot alerts + security updates for dependency vulnerabilities.
# Private vulnerability reporting so users can report issues without public disclosure.
echo "  Enabling security features..."
gh api --method PUT "repos/${REPO}/vulnerability-alerts" --silent
gh api --method PUT "repos/${REPO}/automated-security-fixes" --silent
gh api --method PUT "repos/${REPO}/private-vulnerability-reporting" --silent

# --- Branch protection ---
# Require PRs (0 approvals — solo maintainer, but enforces CI before merge).
# Enforce admins so the maintainer can't bypass accidentally.
# No force pushes or deletions on main.
#
# NOTE: Status check names are NOT set here because they vary per project
# (e.g. "test (3.11)" for Python vs "test" for TypeScript).
# Set them separately:
#   gh api --method PUT repos/OWNER/REPO/branches/main/protection \
#     --input <payload-with-contexts>
echo "  Setting branch protection (without status checks)..."
gh api --method PUT "repos/${REPO}/branches/main/protection" \
  --silent \
  --input - <<'EOF'
{
  "required_status_checks": null,
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF

echo ""
echo "==> Done. Manual steps remaining:"
echo "  1. Set homepage URL:  gh repo edit ${REPO} --homepage <url>"
echo "  2. Set topics:        gh repo edit ${REPO} --add-topic <topic>"
echo "  3. Set status checks: gh api --method PUT repos/${REPO}/branches/main/protection --input <payload>"
echo "  4. Sidebar (About gear icon): uncheck Deployments and Packages if not used"
