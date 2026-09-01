# Social preview cards

The card a `calvindotsg` repository shows when someone pastes a link to it into Slack, X or
LinkedIn. `scripts/social-preview.sh` draws one from the repository's own metadata and uploads it.

```bash
./scripts/social-preview.sh calvindotsg/mac-upkeep      # draw, upload, verify
./scripts/social-preview.sh --render-only calvindotsg/mac-upkeep
./scripts/social-preview.sh --remove calvindotsg/mac-upkeep
```

`images/` holds the PNG last uploaded for each repository. GitHub gives no way to read a social
preview back, so a committed copy is the only record of what is live — and the only way a change
to the design shows up as a diff.

`images/dark/` holds the same six drawn with `--theme dark`. **Nothing uploads these** — the
social preview slot takes one image and it is the light one. They exist because a card is also
useful inside a README, and a README is rendered in the *reader's* theme: a light-canvas PNG on
GitHub's dark mode is a glaring white slab. Embed the pair instead, which GitHub supports and
keeps through its HTML sanitiser (verified: `<picture>`, `<source media>`, `<td width>` and a
wrapping `<a>` all survive, and GitHub wraps the result in its own `themed-picture` element):

```html
<picture>
  <source media="(prefers-color-scheme: dark)"
          srcset="https://raw.githubusercontent.com/calvindotsg/.github/main/social-preview/images/dark/NAME.png">
  <img alt="NAME" src="https://raw.githubusercontent.com/calvindotsg/.github/main/social-preview/images/NAME.png">
</picture>
```

Consumers link to the raw URLs here rather than committing their own copy, so regenerating a
card updates every README that shows it. **Give a card the full column width.** Rendered at
half width in a two-column table it measures ~437px on a desktop and ~180px on a phone, where
everything below the repository name — description, install chip, language row, topics — stops
being readable and the card becomes decoration. GitHub's markdown tables do not stack on narrow
screens, so there is no responsive escape from that.

## What a card carries that GitHub's generated one does not

GitHub's generated card is good and it is live: owner and repository name, description, owner
avatar, four counters — contributors, issues, stars, forks — and a proportional language bar.
Replacing it is only worth doing if the replacement carries more, so this is the comparison, and
it is the reason the card looks the way it does.

| | Generated card | This card |
|---|---|---|
| Name, description, avatar | Yes | Yes |
| Language breakdown | Proportional bar | Proportional bar, as the divider |
| Contributors / issues / stars / forks | All four, live | Stars only, and only when non-zero |
| Licence | No | Yes |
| Topics | No | Yes, up to five |
| **Install command** | **Impossible** | Yes, where one exists |
| Stays current | Automatically | Only when someone re-runs the script |

**The counters are the interesting row, and they are why the answer here differs from the
obvious one.** Losing four live counters sounds like the deciding cost, and on a busy account it
would be. Across the seven repositories these cards cover, those counters total **5 stars, 1 fork
and 0 open issues**, and four of the six read zero across the board. A quarter of the generated
card is given over to an empty scoreboard — which is not neutral, because a reader who scans
`0 · 0 · 0` learns something the repository would rather not lead with. Stars are kept here and
suppressed at zero for exactly that reason.

The language bar is copied deliberately rather than dropped: TypeScript at 81% of a repository
and TypeScript at 51% are different facts, and a single language name cannot tell them apart. It
does double duty as the rule between the prose and the metadata, so an element that would have
been decoration is carrying information instead.

That leaves the install command as the clearest single win, and `repos.json` is where the three
that have one keep it. It is optional: a repository with no entry still gets a card.

## The language bar is only as truthful as linguist's defaults

The bar states a fact about a repository, so it is worth knowing where that fact comes from.
GitHub computes it with [linguist][linguist], which counts bytes of tracked files on the
**default branch**, and by default counts only languages whose type in `languages.yml` is
`programming` or `markup`. `.gitattributes` can override that, per
[linguist's overrides doc][overrides]:

| Attribute | Effect |
|---|---|
| `linguist-detectable` | Count a `prose`/`data` language that is normally ignored, or stop counting one |
| `linguist-documentation` | Exclude — docs |
| `linguist-generated` | Exclude — build output, and hide from diffs |
| `linguist-vendored` | Exclude — code you did not write |
| `linguist-language=NAME` | Reclassify |

The first six repositories were audited against this. **Nothing needed overriding.** No repository
commits build output, nothing is vendored that should not be, and every repository's prose is
already correctly excluded.

**Two exclusions are worth knowing because they are not obvious**, and between them they sank a
change this branch originally carried:

- `vendor.yml` matches `(^|/)\.github/` — **everything under a `.github/` directory is vendored**
  and cannot be counted, whatever `.gitattributes` says about it.
- `documentation.yml` matches `(^|/)README(\.|$)` — every README is documentation, at any depth.

This repository briefly carried `*.md linguist-detectable`, on the reasoning that its
community-health markdown is what the other repositories inherit and so ought to count. The
reasoning was wrong twice over. The pull-request template lives under `.github/` and is vendored,
both READMEs are documentation, so the attribute reached only `SECURITY.md` — and by bytes the
repository really is two large commented shell scripts (52 KB) plus a card template (22 KB)
against 2.5 KB of health files. Markdown lands at 2.9%, or 3.3% even if `.github/**` were
un-vendored as well. "Markdown is what this repository ships" is true of its purpose and false
of its bytes, and linguist measures bytes. The override was removed.

Two repositories that look like candidates and are not, for the record:

- **`portfolio-v2`** — making markdown detectable would let roughly a megabyte of `plans/`
  outweigh the site's own source. Its numbers are already right, though note that ~83% of the
  reported TypeScript is the test suite, which linguist counts as source by design.
- **`calvindotsg`** (the profile README) — would report an accurate and useless `Markdown 100%`
  off a 2 KB README, and would cost the one card in the set that legitimately wears the brand
  accent.

**A merge that changes the language mix invalidates the committed cards.** Linguist reads the
default branch, and there is no notification: re-run the script for any repository whose
languages have moved, or the card will keep asserting the old numbers indefinitely.

[linguist]: https://github.com/github-linguist/linguist
[overrides]: https://github.com/github-linguist/linguist/blob/main/docs/overrides.md

## Status: nothing is uploaded — GitHub cannot serve it

**GitHub's repository-image pipeline is broken as of 2026-08-21.** An upload succeeds, the
repository's `og:image` is repointed at the new asset, GitHub's own Settings page renders the
preview tile — and the bytes never appear on `repository-images.githubusercontent.com`, which
answers 404 indefinitely. A configured repository then unfurls as a *broken image*, which is
worse than the generated card it replaced. So all six cards are drawn, checked and committed
here, and none is uploaded.

This is not specific to this tooling: it reproduces through GitHub's own UI and is reported
publicly — [davep, "GitHub social preview is broken"][davep] (2026-08-21), citing two
community-discussion reports of the same thing. Verified here on `portfolio-v2` across four
uploads: policy `201`, storage `204`, finalize `200`, the asset readable at full size straight
from GitHub's own S3 bucket, and the CDN URL 404 for over 45 minutes. `--remove` put the
generated card back.

**Re-checked 2026-08-23 — still broken.** `mac-upkeep` uploaded cleanly, the asset answered
404 for the full 120-second bound, and the script reverted it and stopped without being asked.
That is the point of the gate: a re-check costs one command and cannot leave a repository
worse than it found it.

### Checking whether it is fixed

**A preflight against somebody else's card does not work, and is worth not re-inventing.**
Every well-known repository's preview was uploaded *before* the outage and keeps serving right
through it, so that probe reports healthy exactly when it matters. The only honest test of
"can GitHub publish a new image" is to publish one and look.

Cheapest first:

1. **Free, and asymmetric.** `curl` an asset orphaned by a failed run. A `200` means the
   pipeline is publishing again. A `404` proves nothing — GitHub may never backfill assets
   orphaned during the outage — so this can confirm recovery but never rule it out.

   ```bash
   curl -so /dev/null -w '%{http_code}\n' \
     https://repository-images.githubusercontent.com/1201444763/a9c7fe1f-2f63-455d-ab22-6f227e9250d0
   ```

2. **Authoritative.** Publish one and look. Safe to run unattended — if the pipeline is still
   broken this uploads, detects it, reverts, and exits non-zero. The cost is a window of at
   most `--serve-timeout` seconds during which one repository unfurls as a broken image.

   ```bash
   ./scripts/social-preview.sh calvindotsg/mac-upkeep
   ```

3. **Roll out**, once step 2 prints `serving after Ns` rather than `NOT serving after Ns`:

   ```bash
   ./scripts/social-preview.sh \
     calvindotsg/portfolio-v2 calvindotsg/mac-upkeep calvindotsg/granola-to-minutes \
     calvindotsg/homebrew-tap calvindotsg/.github calvindotsg/calvindotsg
   ```

## The design

One template, `card.html`, filled per repository. Every value except the install command is read
from the repository at render time. Nothing re-runs this script on its own, so a committed card
is a **snapshot**: a star arriving or a description being edited leaves it stale until someone
runs it again, and a merge that shifts a repository's languages is the most likely
reason it needs re-running.

| | |
|---|---|
| **Palette** | calvin.sg's, token for token, and both of its themes ship. Light is the default — `#FAFAFA` canvas, `#F5F5F5` card, `#E5E5E5` rule, `#0B0B0B` ink, `#A82334` accent. `--theme dark` gives the Mocha set. Not a second brand to maintain. |
| **Type** | SF Pro for prose. FiraCode for the handle, language, licence and topics — every repository here is something you install and run from a shell, so the machine-readable half of the card is set in the font that account's terminal uses. |
| **Signature** | The offset slab behind the card, inherited from the portrait treatment on calvin.sg — and coloured by the repository's **primary language**, not by the brand. The same element that makes the account's cards one set tells them apart before a word is read. The dot beside the language name is the legend. |
| **Install line** | The one element the generated card cannot have. Set as a terminal line — recessed, monospaced, with the prompt in the language colour — because that is where the command will be typed. |
| **Safe area** | GitHub's template asks for a 40pt border its crops may eat. That margin holds the slab and nothing else, so the only croppable element is the only decorative one. |

Language colours come from `primaryLanguage.color` on GitHub's GraphQL API, which returns
linguist's own value. No copy of that table is kept here. A repository with no language — a
profile README — falls back to the brand accent, which is the honest answer for one.

Linguist's palette was picked for a 12px dot on a repository list, not for a 1200px slab, and at
this size some of it disappears — but *which* entries disappear flips with the theme. Measured:
Ruby `#701516` is 1.63:1 against the dark canvas and 11.13:1 against the light one; Shell
`#89e051` is the exact mirror, 11.55:1 on dark and 1.57:1 on light. So the slab steps its colour
*away from the canvas* — toward black on a light ground, toward white on a dark one — until it
clears a low contrast floor. Only the colours that need it move: on the light default, Shell
becomes `#73bc44` at 2.23:1 and every other language is untouched.

The floor is deliberately low. Pushing every language to a common contrast would read more evenly
and destroy the thing the slab is for — Ruby taken that far lands within a few points of the
brand accent, so the tap and the profile README would wear the same colour for opposite reasons.

Two things are measured at render time rather than fixed: the headline steps down until the name
fits on one line, and topics are dropped from the tail until the row fits. Both exist so adding a
repository with a longer name or wordier topics needs no edit here.

`card.html` opens directly in a browser and draws a finished card — it keeps a real sample
between its `__CARD_DATA__` markers, which is what the generator replaces. Work on the design
without running anything.

## How the upload works

GitHub has [no API for this setting][discussion]. Description, homepage and topics are all
writable over REST; the social preview is not. It was absent from `setup-repo.sh`'s
manual-steps checklist altogether until this change — the quieter version of the same problem,
a step nobody automated and nobody was reminded of.

The script drives the requests the Settings page itself makes, from a cmux browser pane that is
signed in to github.com, so the session cookie and the server-rendered CSRF tokens are the real
ones. There are **three** requests, not four:

1. `POST /upload/policies/repository-images` — name, size, content type and repository id, with
   the CSRF token out of `.js-data-upload-policy-url-csrf`. Returns a signed S3 policy and an
   asset record.
2. `POST` to the bucket the policy names, with every field the policy returned, in order, and
   `file` last. The policy is signed over them, and over the exact byte count.
3. `PUT /upload/repository-images/{asset_id}` — marks the asset complete. **This is the step that
   repoints the repository's `og:image`.**

The Settings form is never submitted. Its own JavaScript only updates the Remove button and the
preview tile on completion; posting to `/settings/open-graph-image` answers `404` whatever you
send it. This was read out of GitHub's shipped `behaviors-*.js` rather than guessed.

### Things that are load-bearing, and cost time to learn

- **`Accept: application/json` on the same-origin requests.** Without it the server answers with
  an HTML redirect that `fetch` cannot follow across origins, and WebKit reports it as
  `TypeError: Load failed` — no status, no body, nothing resembling the actual cause.
- **The policy CSRF token is single-use.** A second attempt on the same page load fails with that
  same opaque `Load failed`. Every attempt reloads the settings page first. This is the protocol,
  not a retry policy.
- **The bytes are embedded in the script that uses them.** The settings page keeps re-rendering
  for seconds after it goes interactive, and each re-render wipes page globals, so staging a
  payload on `window` in one call and reading it in the next loses it part-way and surfaces as an
  invalid-base64 error.
- **Wait on the element, never on the load state.** This page reports `complete` long before its
  sections have streamed in and reports `interactive` indefinitely afterwards.
- **cmux surface UUIDs, not `surface:N`.** Short refs are positional and get reassigned as panes
  come and go; a stored one goes stale mid-run as `Surface is not a browser`.
- **Simulating the UI does not work here.** cmux's WebKit does not upgrade GitHub's custom
  elements, so `<file-attachment>` is an inert `HTMLElement`: assigning `input.files` through a
  `DataTransfer` and dispatching `change` does nothing. Driving the protocol is not the
  roundabout option, it is the only one — and it fails loudly instead of silently.

[davep]: https://blog.davep.org/2026/08/21/github-social-preview-is-broken.html
[discussion]: https://github.com/orgs/community/discussions/32166
