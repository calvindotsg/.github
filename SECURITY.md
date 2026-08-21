# Security Policy

## Supported Versions

This file is inherited by every `calvindotsg` repository that does not define its own, and most of
them cut no releases at all. So there is no fleet-wide version policy to state: **assume only the
default branch is maintained** unless a repository's own README or releases page says otherwise.
Where a project does publish releases, security fixes go to the latest minor release.

## Reporting a Vulnerability

**Please use [GitHub Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)** to report security issues.

1. Go to the repository's **Security** tab
2. Click **"Report a vulnerability"**
3. Fill out the advisory form

**If the Security tab shows no "Report a vulnerability" button** — which is the case on private
repositories, where GitHub does not offer the feature, and on any public repository where it has
not been enabled — email <security@calvin.sg> instead. Please do not open a public issue for a
security report.

Machine-readable contact details for the website are published at
<https://calvin.sg/.well-known/security.txt>.

These are personal projects maintained by one person, so treat response time as best-effort
rather than a guarantee: the aim is to acknowledge within **7 days**. Reports are worked through
with the reporter and a fix coordinated before any public disclosure.

## Scope

This policy applies to every repository under `calvindotsg`.

Most are local CLI tools that read files on your machine, where the relevant risks are unintended file access, privilege escalation, and arbitrary code execution. Two categories behave differently and are worth calling out explicitly:

- **The Homebrew tap** installs software, so install-time integrity is in scope: checksum handling, download sources, and any code that runs during installation.
- **The website** is deployed, calls third-party APIs, and ships analytics. It does make outbound network requests and does handle credentials in its build pipeline.

Please do not assume a given project is network-isolated — check its README before relying on that.
