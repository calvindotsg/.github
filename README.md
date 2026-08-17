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

To check whether a repo has actually had the baseline applied:

```bash
gh api repos/calvindotsg/<repo-name>/branches/main/protection
```

A `404 Branch not protected` means it has not.

## Related

- **Template repos** (private): scaffold new projects with boilerplate code
- **This repo**: provides community files + settings script
- **Workflow**: create from template → run setup script → set project-specific values
