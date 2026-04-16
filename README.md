# .github

Default [community health files](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file) for `calvindotsg` repositories.

## What This Provides

Files in this repository are automatically inherited by all `calvindotsg` repos that don't define their own versions:

| File | Purpose |
|------|---------|
| `SECURITY.md` | Security policy — report via GitHub Private Vulnerability Reporting |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR checklist — conventional commits, tests, linter |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | Generic bug report form |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | Generic feature request form |

## Override Behavior

- **PR template**: A repo's own `.github/PULL_REQUEST_TEMPLATE.md` takes precedence.
- **Issue templates**: Override is **directory-level** — if a repo has its own `.github/ISSUE_TEMPLATE/` directory, ALL templates from this repo are ignored. Include a complete set of templates in the override.
- **SECURITY.md**: A repo's own `SECURITY.md` (root or `.github/`) takes precedence.

## Setup Script

`scripts/setup-repo.sh` applies universal repository settings via `gh` CLI:

- Squash-only merges with PR title + description
- Auto-delete branches on merge, auto-merge enabled
- Wiki and Projects disabled
- Dependabot alerts and security updates
- Private vulnerability reporting
- Branch protection (PRs required, enforce admins, no force push)

```bash
./scripts/setup-repo.sh calvindotsg/<repo-name>
```

Project-specific settings (homepage, topics, status check names) are set separately — the script prints a reminder.

## Related

- **Template repos** (`template-python-cli`, `template-typescript-cli`): scaffold new projects with boilerplate code
- **This repo**: provides community files + settings script
- **Workflow**: create from template → run setup script → set project-specific values
