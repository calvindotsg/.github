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

## Which repositories should have one — and why most should not

**GitHub's generated card is good, and it is live.** Measured against
`calvindotsg/portfolio-v2`, it carries the owner and repository name, the description, the
owner's avatar, four counters — contributors, issues, stars, forks — and a proportional language
bar, and every one of those numbers updates itself.

A custom card built from the same metadata is therefore not a reskin, it is a **downgrade**:

| | Generated card | A metadata-only custom card |
|---|---|---|
| Name, description | Yes | Yes |
| Owner avatar | Yes | No |
| Contributors / issues / stars / forks | Yes, **live** | Stars only, **frozen at render time** |
| Language | Proportional bar over every language | Primary language only |
| Licence, topics | No | Yes |
| Stays current | Automatically | Only when someone re-runs this script |

Net of that trade, a metadata-only card adds a licence and three topics and gives up four live
counters, the avatar and the full language breakdown. That is not worth doing, and the reason to
write it down is that it is not obvious until the two are put side by side.

**So a repository earns a card only when the card can carry something the generated one
structurally cannot.** Today that means one thing: an **install command**. It exists in no GitHub
metadata field, so no generated card can ever show it, and for something you install and run it
is the most useful line on the card. `repos.json` is the list, and having an entry there is what
makes a repository eligible — `social-preview.sh` refuses to upload for a repository without one
unless you pass `--force`.

Three qualify. `portfolio-v2`, `.github` and the `calvindotsg` profile README do not, and keep
GitHub's card. For `portfolio-v2` the thing that would beat the default is a **screenshot of
calvin.sg** — showing the product rather than restating its metadata. That is a different kind of
card and is not built here.

## Status: nothing is uploaded, and GitHub could not serve it anyway

Two independent reasons, either sufficient on its own:

1. **The eligibility rule above.** Only three repositories qualify, and their cards are committed
   here, drawn and checked, ready to upload.
2. **GitHub's repository-image pipeline is broken as of 2026-08-21.** An upload succeeds, the
   repository's `og:image` is repointed at the new asset, GitHub's own Settings page renders the
   preview tile — and the bytes never appear on `repository-images.githubusercontent.com`, which
   answers 404 indefinitely. A configured repository then unfurls as a *broken image*, which is
   worse than the generated card it replaced.

The second is not specific to this tooling: it reproduces through GitHub's own UI and is reported
publicly — [davep, "GitHub social preview is broken"][davep] (2026-08-21), citing two
community-discussion reports of the same thing. Verified here on `portfolio-v2` across four
uploads: policy `201`, storage `204`, finalize `200`, the asset readable at full size straight
from GitHub's own S3 bucket, and the CDN URL 404 for over 45 minutes. `--remove` put the
generated card back.

**To roll out once GitHub fixes it**, confirm on one repository that the script prints
`set and serving` rather than `serving: not yet`, then:

```bash
./scripts/social-preview.sh \
  calvindotsg/mac-upkeep calvindotsg/granola-to-minutes calvindotsg/homebrew-tap
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
