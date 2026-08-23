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
would be. Across the six repositories these cards cover, those counters total **5 stars, 1 fork
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

## The language bar is only as truthful as each repo's `.gitattributes`

The bar states a fact about a repository, so it is worth knowing where that fact comes from and
how it goes wrong. GitHub computes it with [linguist][linguist], which counts bytes of tracked
files on the **default branch** and, by default, counts only languages whose type in
`languages.yml` is `programming` or `markup`. Everything else — `prose` and `data` — is
invisible. `.gitattributes` is the documented way to correct that, per
[linguist's overrides doc][overrides]:

| Attribute | Effect |
|---|---|
| `linguist-detectable` | Count a `prose`/`data` language that is normally ignored, or stop counting one |
| `linguist-documentation` | Exclude — docs |
| `linguist-generated` | Exclude — build output, and hide from diffs |
| `linguist-vendored` | Exclude — code you did not write |
| `linguist-language=NAME` | Reclassify |

All six repositories were audited against this. **No vendored, generated or documentation files
are being miscounted anywhere** — nothing commits build output, and every repository's markdown
is already correctly excluded as prose. One defect was real:

**`prose` invisibility on a repository whose product is prose.** This repository's markdown —
`SECURITY.md`, the pull-request template, the issue forms — is exactly what the other
repositories inherit, and it counted for nothing. Adding `social-preview/card.html` would have
made that worse, reporting a community-health repository as Shell 68% / HTML 32% with the
markdown still invisible. `.gitattributes` here now carries `*.md linguist-detectable`, giving
Shell 52% / HTML 24% / Markdown 24%.

**It is a per-repository judgement, not a setting to standardise.** Two repositories that look
like the same case are not:

- **`portfolio-v2`** — the same line would let roughly a megabyte of `plans/` outweigh the site's
  own source. Deliberately not applied. Its numbers are already correct: TypeScript genuinely
  dominates, though note ~83% of that TypeScript is the test suite, which linguist counts as
  source by design.
- **`calvindotsg`** (the profile README) — the same line would report `Markdown 100%` off a 2 KB
  README. Deliberately not applied either, for the opposite reason: it is *accurate* and tells a
  reader nothing, and it would trade the one card in the set that legitimately wears the brand
  accent for linguist's Markdown blue. One line in that repository if this is ever wanted.

**A merge that changes the language mix invalidates the committed cards.** Linguist reads the
default branch, so this repository's own card still shows `Shell` alone until this branch lands
and GitHub re-indexes. Re-run the script for any repository whose languages have moved — there
is no notification, and the card will keep asserting the old numbers indefinitely.

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

**To roll out once GitHub fixes it**, confirm on one repository that the script prints
`set and serving` rather than `serving: not yet`, then:

```bash
./scripts/social-preview.sh \
  calvindotsg/portfolio-v2 calvindotsg/mac-upkeep calvindotsg/granola-to-minutes \
  calvindotsg/homebrew-tap calvindotsg/.github calvindotsg/calvindotsg
```

## The design

One template, `card.html`, filled per repository. Every value except the install command is read
from the repository at render time. Nothing re-runs this script on its own, so a committed card
is a **snapshot**: a star arriving or a description being edited leaves it stale until someone
runs it again. That is the cost the eligibility rule above is paying for.

| | |
|---|---|
| **Palette** | calvin.sg's dark theme, token for token — `#111111` canvas, `#171717` card, `#2C2C2C` rule, `#FAFAFA` ink, `#F3A3AA` accent. Not a second brand to maintain. |
| **Type** | SF Pro for prose. FiraCode for the handle, language, licence and topics — every repository here is something you install and run from a shell, so the machine-readable half of the card is set in the font that account's terminal uses. |
| **Signature** | The offset slab behind the card, inherited from the portrait treatment on calvin.sg — and coloured by the repository's **primary language**, not by the brand. The same element that makes the account's cards one set tells them apart before a word is read. The dot beside the language name is the legend. |
| **Install line** | The one element the generated card cannot have. Set as a terminal line — recessed, monospaced, with the prompt in the language colour — because that is where the command will be typed. |
| **Safe area** | GitHub's template asks for a 40pt border its crops may eat. That margin holds the slab and nothing else, so the only croppable element is the only decorative one. |

Language colours come from `primaryLanguage.color` on GitHub's GraphQL API, which returns
linguist's own value. No copy of that table is kept here. A repository with no language — a
profile README — falls back to the brand accent, which is the honest answer for one.

Linguist's palette was picked for a 12px dot on a white list, and some of it disappears on a
near-black canvas: Ruby's `#701516` sits at 0.02 relative luminance. The slab lifts a colour
toward white only until it clears a low floor, so dark red stays dark red. Lifting every language
to a common luminance would read more evenly and destroy the point — Ruby lifted that far lands
within a few points of the brand pink, so the tap and the profile README would wear the same
colour for opposite reasons.

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
