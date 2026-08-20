# Security Policy

## Supported Versions

Only the latest minor release is supported with security updates.

## Reporting a Vulnerability

**Please use [GitHub Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)** to report security issues.

1. Go to the repository's **Security** tab
2. Click **"Report a vulnerability"**
3. Fill out the advisory form

**If the Security tab shows no "Report a vulnerability" button** — which is the case on private
repositories, where GitHub does not offer the feature, and on any public repository where it has
not been enabled — email <security@calvin.sg> instead. Please do not open a public issue for a
security report.

You will receive an acknowledgment within **7 days**. We will work with you to understand the issue and coordinate a fix before any public disclosure.

## Scope

This policy applies to every repository under `calvindotsg`.

Most are local CLI tools that read files on your machine, where the relevant risks are unintended file access, privilege escalation, and arbitrary code execution. Two categories behave differently and are worth calling out explicitly:

- **The Homebrew tap** installs software, so install-time integrity is in scope: checksum handling, download sources, and any code that runs during installation.
- **The website** is deployed, calls third-party APIs, and ships analytics. It does make outbound network requests and does handle credentials in its build pipeline.

Please do not assume a given project is network-isolated — check its README before relying on that.
