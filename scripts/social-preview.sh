#!/usr/bin/env bash
set -euo pipefail

# Generate and publish a repository's social preview card.
#
# GitHub exposes no API for this setting. Description, homepage and topics are all writable
# over REST, so a repository can be provisioned end to end — and then the one field that
# decides what a link to it looks like in Slack, on LinkedIn and in a tweet has to be set by
# hand in Settings. It was missing from `setup-repo.sh`'s manual-steps checklist entirely,
# which is the quieter version of the same problem: a step nobody automated and nobody was
# reminded of. That checklist now names it, and points here.
#
# This closes it by driving the same requests the Settings page makes, from inside a real
# authenticated browser session. It is not a screenshot of a form being filled in: the card is
# rendered from the repository's own metadata, and the upload is GitHub's own three-request
# upload protocol, issued by `fetch` from the settings page's origin so the session cookie and
# the server-rendered CSRF tokens are the ones the server expects. See social-preview/README.md
# for the protocol, and for what is load-bearing about each step.
#
# Usage:
#   ./scripts/social-preview.sh OWNER/REPO [OWNER/REPO ...]
#   ./scripts/social-preview.sh --render-only calvindotsg/mac-upkeep
#
# Flags:
#   --render-only        Draw the card and stop. Nothing is uploaded and nothing is verified.
#   --remove             Delete the repository's uploaded card, restoring GitHub's generated
#                        default. The escape hatch for a card that is set but not being served.
#   --force              Upload without requiring the card to actually be served, and without
#                        reverting when it is not. Read the refusal it overrides first.
#   --serve-timeout N    Seconds to wait for GitHub to publish an uploaded card (default 120).
#   --theme light|dark   Which of calvin.sg's two themes to draw. Default light — see the note
#                        at the top of card.html for why a static card has to pick one.
#   --images-dir DIR     Where the PNG lands (default: social-preview/images).
#   --template FILE      Card template (default: social-preview/card.html).
#   --keep-surfaces      Leave the cmux browser panes open afterwards, to look at them.
#
# Requires: macOS, cmux (running, with this script started from one of its terminals),
# gh (authenticated as an account that can administer the repository), python3, curl.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

TEMPLATE="${ROOT_DIR}/social-preview/card.html"
IMAGES_DIR="${ROOT_DIR}/social-preview/images"
OVERRIDES="${ROOT_DIR}/social-preview/repos.json"
RENDER_ONLY=0
REMOVE=0
FORCE=0
THEME="light"
SERVE_TIMEOUT=120
KEEP_SURFACES=0
REPOS=()

RENDER_SURFACE=""
UPLOAD_SURFACE=""
WORK_DIR=""

# GitHub rejects anything above 1 MB, and its own guidance asks for 1280x640.
MAX_BYTES=1048576
CARD_W=1280
CARD_H=640

# --- Reporting -------------------------------------------------------------------------------
# Each repository is several irreversible-ish writes in sequence — a card is uploaded, and the
# repository's open-graph image is repointed at it. An abort part-way leaves the account in a
# mixed state with nothing to say so, which is the failure `setup-repo.sh` reports the same way.
STEP="startup"
step() { STEP="$1"; printf '  %s...\n' "$1"; }
say() { printf '%s\n' "$*"; }
die() { printf '\n!! %s\n' "$*" >&2; exit 1; }

# ${LINENO} in an ERR trap reports the LAST line of a multi-line command, so it is a hint rather
# than a location. Held in a function so the trap string stays a single-quoted literal — and so
# it can be re-armed by name after the one place that deliberately disarms it.
on_err() {
  printf '\n!! ABORTED during: %s (near line %s)\n' "${STEP}" "$1" >&2
  printf '   Repositories processed before this point were configured; later ones were not.\n' >&2
  printf '   Re-running is safe: every step is a full replacement, not an append.\n' >&2
}
trap 'on_err ${LINENO}' ERR

cleanup() {
  [ -n "${WORK_DIR}" ] && [ -d "${WORK_DIR}" ] && rm -rf "${WORK_DIR}"
  if [ "${KEEP_SURFACES}" -eq 0 ]; then
    [ -n "${RENDER_SURFACE}" ] && cmux close-surface --surface "${RENDER_SURFACE}" >/dev/null 2>&1 || true
    [ -n "${UPLOAD_SURFACE}" ] && cmux close-surface --surface "${UPLOAD_SURFACE}" >/dev/null 2>&1 || true
  fi
  return 0
}
trap cleanup EXIT

# --- Arguments -------------------------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --render-only)   RENDER_ONLY=1; shift ;;
    --remove)        REMOVE=1; shift ;;
    --force)         FORCE=1; shift ;;
    --theme)         THEME="${2:?--theme needs light or dark}"; shift 2 ;;
    --serve-timeout) SERVE_TIMEOUT="${2:?--serve-timeout needs seconds}"; shift 2 ;;
    --keep-surfaces) KEEP_SURFACES=1; shift ;;
    --images-dir)    IMAGES_DIR="${2:?--images-dir needs a path}"; shift 2 ;;
    --template)      TEMPLATE="${2:?--template needs a path}"; shift 2 ;;
    -h|--help)       sed -n '3,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*)              die "Unknown flag: $1" ;;
    *)               REPOS+=("$1"); shift ;;
  esac
done

[ "${#REPOS[@]}" -gt 0 ] || die "Usage: $0 [flags] OWNER/REPO [OWNER/REPO ...]"

# --- Preflight -------------------------------------------------------------------------------
step "Checking prerequisites"
for tool in cmux gh python3 curl; do
  command -v "${tool}" >/dev/null 2>&1 || die "${tool} is not on PATH."
done
[ -f "${TEMPLATE}" ] || die "Template not found: ${TEMPLATE}"

# cmux only accepts socket connections from processes it spawned. Run from a cmux terminal.
[ -n "${CMUX_WORKSPACE_ID:-}" ] || die "\$CMUX_WORKSPACE_ID is unset — run this from a cmux terminal."
cmux ping >/dev/null 2>&1 || die "cmux is not answering on its socket. If it just auto-updated, quit and reopen it."

gh auth status >/dev/null 2>&1 || die "gh is not authenticated. Run: gh auth status"

mkdir -p "${IMAGES_DIR}"
WORK_DIR="$(mktemp -d)"

# --- Browser surfaces --------------------------------------------------------------------------
# UUIDs, not short refs. `surface:3` is positional and is silently reassigned when panes come and
# go, so a long-running script that stored one can end up driving somebody else's pane — or, more
# often, a pane that no longer exists, which surfaces as "Surface is not a browser" several
# minutes into a run.
open_surface() {
  cmux --id-format uuids --json browser open "$1" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["surface_id"])'
}

# A repository whose name begins with a dot would otherwise be written as a hidden file that
# never shows up in a directory listing or a plain `ls`. `.github` becomes `dot-github.png`.
image_name() { case "$1" in .*) printf 'dot-%s' "${1#.}" ;; *) printf '%s' "$1" ;; esac; }

# --- Render ------------------------------------------------------------------------------------
metadata_json() {
  # shellcheck disable=SC2016  # $o and $n are GraphQL variables, and must reach gh unexpanded.
  gh api graphql \
    -f query='query($o:String!,$n:String!){repository(owner:$o,name:$n){
        name description stargazerCount isArchived isPrivate
        owner{avatarUrl(size:400)}
        primaryLanguage{name color}
        languages(first:10,orderBy:{field:SIZE,direction:DESC}){edges{size node{name color}}}
        licenseInfo{spdxId}
        repositoryTopics(first:10){nodes{topic{name}}}
      }}' \
    -f o="$1" -f n="$2"
}

render_card() {
  local owner="$1" name="$2" out="$3" meta="${WORK_DIR}/meta.json" html="${WORK_DIR}/card.html"

  metadata_json "${owner}" "${name}" > "${meta}"

  OWNER="${owner}" THEME="${THEME}" python3 - "${meta}" "${TEMPLATE}" "${html}" "${OVERRIDES}" <<'PY'
import base64, json, os, re, sys, urllib.request

meta, template, out, overrides = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
repo = json.load(open(meta))["data"]["repository"]
if repo is None:
    sys.exit("repository not found")

lang = repo.get("primaryLanguage") or {}
lic = repo.get("licenseInfo") or {}
card = {
    "owner": os.environ["OWNER"],
    "name": repo["name"],
    "description": repo.get("description") or "",
    "language": lang.get("name"),
    "languageColor": lang.get("color"),
    "license": lic.get("spdxId"),
    "stars": repo.get("stargazerCount") or 0,
    "topics": [n["topic"]["name"] for n in repo["repositoryTopics"]["nodes"]],
    "theme": os.environ.get("THEME", "light"),
    "languages": [
        {"name": e["node"]["name"], "color": e["node"]["color"], "size": e["size"]}
        for e in (repo.get("languages") or {}).get("edges", [])
    ],
}

# The avatar is inlined as a data URI rather than linked. The renderer would happily fetch the
# remote URL, but then the screenshot silently depends on the network being up at that moment —
# and a card that renders with a missing image is a card that still passes every dimension check
# below. Fetching it here means a failure is an error, not a blank rectangle.
avatar_url = (repo.get("owner") or {}).get("avatarUrl")
if avatar_url:
    with urllib.request.urlopen(avatar_url, timeout=20) as fh:
        blob = fh.read()
        ctype = fh.headers.get_content_type() or "image/png"
    card["avatar"] = f"data:{ctype};base64," + base64.b64encode(blob).decode()

# The install command is the only field not read from GitHub, because GitHub has nowhere to
# keep it. Its presence is also the eligibility test — see the note at the top of repos.json.
extra = {}
if os.path.exists(overrides):
    with open(overrides, encoding="utf-8") as fh:
        extra = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
entry = extra.get(f"{os.environ['OWNER']}/{repo['name']}", {})
if entry.get("install"):
    card["install"] = entry["install"]

# A per-repository theme overrides the run-wide default. Light is the default everywhere; a
# repository only names a theme here when there is a reason on the card, not a mood.
if entry.get("theme"):
    card["theme"] = entry["theme"]

html = open(template, encoding="utf-8").read()
# The template keeps a real sample between these markers so it draws a finished card when
# opened directly. Replacing the region rather than the whole script block is what lets the
# design be worked on without running this script.
pattern = re.compile(r"/\*__CARD_DATA__\*/.*?/\*__END_CARD_DATA__\*/", re.S)
if not pattern.search(html):
    sys.exit("template is missing its __CARD_DATA__ markers")
payload = "/*__CARD_DATA__*/ " + json.dumps(card, ensure_ascii=False) + " /*__END_CARD_DATA__*/"
open(out, "w", encoding="utf-8").write(pattern.sub(lambda _: payload, html, count=1))

print(json.dumps({"archived": repo["isArchived"], "private": repo["isPrivate"]}))
PY
}

screenshot_card() {
  local html="$1" out="$2"
  cmux browser "${RENDER_SURFACE}" goto "file://${html}" >/dev/null
  cmux browser "${RENDER_SURFACE}" viewport "${CARD_W}" "${CARD_H}" >/dev/null
  # The two measured passes in the template (headline fitting, topic dropping) settle after
  # `load`, and a screenshot taken between them catches the headline mid-resize. The template
  # raises this flag once `document.fonts.ready` has resolved and both passes are done.
  cmux browser "${RENDER_SURFACE}" wait \
      --function "document.documentElement.dataset.cardReady==='1'" --timeout-ms 20000 >/dev/null

  # A LAYOUT CHECK THAT CAN FAIL, because the last one could not. The language bar is a 7px flex
  # item in a column, so when the card ran out of room it shrank to zero rather than overflowing
  # — and it did that only on the cards carrying an install chip, which are the tallest. Every
  # other check still passed: the PNG was 1280x640, under a megabyte, and looked fine unless you
  # compared two cards side by side. Measuring the drawn element is the only thing that catches
  # an element that renders at zero height.
  local layout
  layout="$(cmux browser "${RENDER_SURFACE}" eval '(() => {
    const bar = document.getElementById("langbar");
    const name = document.getElementById("name");
    const chip = document.getElementById("install");
    return JSON.stringify({
      barWanted: !bar.hidden,
      barHeight: bar.getBoundingClientRect().height,
      nameHeight: name.getBoundingClientRect().height,
      chipWanted: !chip.hidden,
      chipHeight: chip.getBoundingClientRect().height
    });
  })()' 2>&1)"
  printf '%s' "${layout}" | python3 -c '
import json, sys
d = json.loads(sys.stdin.read())
bar, chip, name = d["barHeight"], d["chipHeight"], d["nameHeight"]
if d["barWanted"] and bar < 6:
    sys.exit("language bar collapsed to " + str(bar) + "px - the card ran out of vertical room")
if d["chipWanted"] and chip < 40:
    sys.exit("install chip collapsed to " + str(chip) + "px")
if name < 40:
    sys.exit("headline collapsed to " + str(name) + "px")
'

  cmux browser "${RENDER_SURFACE}" screenshot --out "${out}" >/dev/null
}

# A screenshot command that returns OK having written a 0-byte file, or a file at the pane's
# native size because viewport emulation was refused, is the failure this catches. Read the
# dimensions out of the PNG header rather than trusting the request.
assert_png() {
  local f="$1"
  MAX_BYTES="${MAX_BYTES}" CARD_W="${CARD_W}" CARD_H="${CARD_H}" python3 - "${f}" <<'PY'
import os, struct, sys
p = sys.argv[1]
d = open(p, "rb").read()
if d[:8] != b"\x89PNG\r\n\x1a\n":
    sys.exit(f"{p}: not a PNG")
w, h = struct.unpack(">II", d[16:24])
want = (int(os.environ["CARD_W"]), int(os.environ["CARD_H"]))
if (w, h) != want:
    sys.exit(f"{p}: rendered {w}x{h}, expected {want[0]}x{want[1]}")
if len(d) > int(os.environ["MAX_BYTES"]):
    sys.exit(f"{p}: {len(d)} bytes exceeds GitHub's 1 MB limit")
print(f"{w}x{h}, {len(d)} bytes")
PY
}

# --- Upload ------------------------------------------------------------------------------------
# One attempt per page load, and that is not a retry policy — it is the protocol.
#
# The policy endpoint's CSRF token is single-use. Spend it and the next POST is answered with a
# redirect to another origin, which `fetch` reports as `TypeError: Load failed` — no status, no
# body, nothing that reads like "stale token". Every attempt therefore starts by reloading the
# settings page to mint fresh tokens.
upload_card() {
  local owner="$1" name="$2" png="$3" js="${WORK_DIR}/upload.js"

  python3 - "${png}" "${js}" "$(image_name "${name}").png" <<'PY'
import base64, json, sys
png, out, filename = sys.argv[1], sys.argv[2], sys.argv[3]
b64 = base64.b64encode(open(png, "rb").read()).decode()

# The bytes are embedded in the script itself rather than staged on `window` by an earlier
# call. The settings page streams in and re-renders for some seconds after it becomes
# interactive, and each re-render wipes page globals — a two-call transfer loses its payload
# somewhere in the middle and reports an empty string as an invalid-base64 error.
js = """(async () => {
  const o = {trace: []};
  try {
    const B64 = %s;
    const NAME = %s;
    const fa = document.querySelector("file-attachment[data-upload-policy-url]");
    if (!fa) return JSON.stringify({error: "settings page has no upload element (not signed in, or no admin access)"});

    const bin = atob(B64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    o.bytes = arr.length;

    // 1. Ask for an upload policy. Accept: application/json is load-bearing — without it the
    //    server answers with an HTML redirect that fetch cannot follow across origins.
    const pf = new FormData();
    pf.append("authenticity_token", fa.querySelector(".js-data-upload-policy-url-csrf").value);
    pf.append("content_type", "image/png");
    pf.append("name", NAME);
    pf.append("size", String(arr.length));
    pf.append("repository_id", fa.getAttribute("data-upload-repository-id"));
    // GitHub's own client stamps every same-origin request in this flow with
    // GitHub-Verified-Fetch. Sent here for the same reason the Accept header is: to be the
    // request the server is expecting rather than one that merely resembles it.
    const H = {Accept: "application/json", "GitHub-Verified-Fetch": "true"};
    const pr = await fetch(fa.getAttribute("data-upload-policy-url"),
      {method: "POST", body: pf, credentials: "same-origin", headers: H});
    o.trace.push("policy " + pr.status);
    if (!pr.ok) { o.error = "policy " + pr.status + ": " + (await pr.text()).slice(0, 200); return JSON.stringify(o); }
    const p = await pr.json();
    o.assetId = p.asset.id;
    o.href = p.asset.href;

    // 2. Put the bytes in the bucket the policy names. Every field the policy returned has to
    //    go in, in order, with `file` last — the policy is signed over them.
    const sf = new FormData();
    for (const k of Object.keys(p.form)) sf.append(k, p.form[k]);
    if (p.same_origin) sf.append("authenticity_token", p.upload_authenticity_token);
    sf.append("file", new Blob([arr], {type: "image/png"}), NAME);
    const sr = await fetch(p.upload_url, {method: "POST", body: sf});
    o.trace.push("storage " + sr.status);
    if (sr.status >= 400) { o.error = "storage " + sr.status + ": " + (await sr.text()).slice(0, 200); return JSON.stringify(o); }

    // 3. Tell GitHub the asset is complete. THIS is the step that repoints the repository's
    //    open-graph image — there is no fourth request. The Settings form is never submitted;
    //    its own JS only updates the Remove button and the preview tile.
    const af = new FormData();
    af.append("authenticity_token", p.asset_upload_authenticity_token);
    const ar = await fetch(p.asset_upload_url,
      {method: "PUT", body: af, credentials: "same-origin", headers: H});
    o.trace.push("asset " + ar.status);
    if (ar.status >= 400) { o.error = "asset " + ar.status + ": " + (await ar.text()).slice(0, 200); return JSON.stringify(o); }
    o.ok = true;
    return JSON.stringify(o);
  } catch (e) {
    o.error = String(e && e.message);
    return JSON.stringify(o);
  }
})()""" % (json.dumps(b64), json.dumps(filename))
open(out, "w", encoding="utf-8").write(js)
PY

  local attempt result
  for attempt in 1 2 3; do
    cmux browser "${UPLOAD_SURFACE}" goto "https://github.com/${owner}/${name}/settings" >/dev/null
    # Wait on the element, never on the load state: this page reports `complete` long before
    # the settings sections have streamed in, and reports `interactive` indefinitely after.
    if ! cmux browser "${UPLOAD_SURFACE}" wait \
           --selector "file-attachment[data-upload-policy-url]" --timeout-ms 45000 >/dev/null 2>&1; then
      say "     attempt ${attempt}: settings form never appeared"
      continue
    fi
    result="$(cmux browser "${UPLOAD_SURFACE}" eval "$(cat "${js}")" 2>&1 || true)"
    if printf '%s' "${result}" | python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.stdin.read()).get("ok") else 1)' 2>/dev/null; then
      printf '%s' "${result}"
      return 0
    fi
    say "     attempt ${attempt}: ${result}"
  done
  return 1
}

remove_card() {
  local owner="$1" name="$2"
  cmux browser "${UPLOAD_SURFACE}" goto "https://github.com/${owner}/${name}/settings" >/dev/null
  cmux browser "${UPLOAD_SURFACE}" wait \
      --selector "file-attachment[data-upload-policy-url]" --timeout-ms 45000 >/dev/null 2>&1 || return 1
  cmux browser "${UPLOAD_SURFACE}" eval '(async () => {
    try {
      const f = [...document.querySelectorAll("form")].filter(x =>
        (x.getAttribute("action") || "").endsWith("open-graph-image") &&
        [...x.elements].some(e => e.name === "_method" && e.value === "delete"))[0];
      if (!f) return JSON.stringify({error: "no delete form — nothing is set"});
      const fd = new FormData();
      for (const e of f.elements) if (e.name) fd.append(e.name, e.value);
      const r = await fetch(f.getAttribute("action"), {method: "POST", body: fd,
        credentials: "same-origin", headers: {"GitHub-Verified-Fetch": "true"}});
      return JSON.stringify({status: r.status, ok: r.status < 400});
    } catch (e) { return JSON.stringify({error: String(e && e.message)}); }
  })()'
}

# --- Verify ------------------------------------------------------------------------------------
# Read the answer off the public repository page, not off the settings page we just wrote to.
#
# Two states are reported separately because they genuinely are two things, and collapsing them
# would produce a check that cannot fail: `set` means the repository's og:image now points at an
# uploaded asset instead of GitHub's generated default, and `serving` means the CDN has finished
# publishing the bytes behind it. The first is this script's doing and is a hard gate. The second
# is a GitHub-side job on its own clock.
verify_card() {
  local owner="$1" name="$2" og code waited=0
  og="$(curl -sS "https://github.com/${owner}/${name}" 2>/dev/null \
        | grep -oE 'https://repository-images\.githubusercontent\.com/[^"]+' | head -1 || true)"
  if [ -z "${og}" ]; then
    say "     og:image is still GitHub's generated default — the upload did not take"
    return 1
  fi
  say "     set: ${og}"

  # POLL UNTIL THE BYTES ARE ACTUALLY SERVED, because every status code up to this point lies.
  # The policy returns 201, storage 204, the finalize 200, GitHub repoints og:image and renders
  # the preview tile in its own Settings page — and the asset can still 404 forever on the CDN.
  # That was the whole of the 2026-08-21 outage, and nothing the script sends can detect it.
  #
  # A preflight against somebody else's card was tried first and is WRONG: every well-known
  # repository's preview was uploaded before the outage and keeps serving throughout it, so the
  # probe reports healthy exactly when it matters. The only honest test of "can GitHub publish a
  # new image" is to publish one and look.
  while [ "${waited}" -lt "${SERVE_TIMEOUT}" ]; do
    code="$(curl -sS -o /dev/null --max-time 15 -w '%{http_code}' "${og}" 2>/dev/null || echo 000)"
    if [ "${code}" = "200" ]; then
      say "     serving after ${waited}s"
      return 0
    fi
    sleep 10
    waited=$((waited + 10))
  done

  say "     NOT serving after ${SERVE_TIMEOUT}s (CDN returns ${code})"
  return 2
}

# --- Main ---# --- Main --------------------------------------------------------------------------------------
say "==> Social preview: ${#REPOS[@]} repository(ies)"

step "Opening a browser pane to render in"
RENDER_SURFACE="$(open_surface "about:blank")"
[ -n "${RENDER_SURFACE}" ] || die "Could not open a cmux browser surface."

if [ "${RENDER_ONLY}" -eq 0 ]; then
  step "Opening a browser pane to upload from"
  UPLOAD_SURFACE="$(open_surface "https://github.com/settings/profile")"
  [ -n "${UPLOAD_SURFACE}" ] || die "Could not open a cmux browser surface."
  cmux browser "${UPLOAD_SURFACE}" wait --load-state complete --timeout-ms 30000 >/dev/null 2>&1 || true
  # A signed-out session is not an error page here — GitHub answers 404 for a settings URL it
  # will not show you, so "Page not found" is what being logged out looks like.
  if cmux browser "${UPLOAD_SURFACE}" get title 2>/dev/null | grep -q "Page not found"; then
    say "     not signed in — importing github.com cookies from a local browser"
    cmux browser import --non-interactive --domain github.com 2>&1 | sed 's/^/     /' || true
    cmux browser "${UPLOAD_SURFACE}" goto "https://github.com/settings/profile" >/dev/null
    cmux browser "${UPLOAD_SURFACE}" wait --load-state complete --timeout-ms 30000 >/dev/null 2>&1 || true
    cmux browser "${UPLOAD_SURFACE}" get title 2>/dev/null | grep -q "Page not found" &&
      die "Still signed out. Sign in to github.com in the cmux browser pane, then re-run."
  fi
fi

FAILED=0
for full in "${REPOS[@]}"; do
  owner="${full%%/*}"
  name="${full#*/}"
  [ "${owner}" != "${full}" ] && [ -n "${name}" ] || die "Expected OWNER/REPO, got: ${full}"

  say ""
  say "--> ${owner}/${name}"

  step "Reading repository metadata"
  state="$(render_card "${owner}" "${name}" "${IMAGES_DIR}/$(image_name "${name}").png")"

  if printf '%s' "${state}" | grep -q '"archived": true'; then
    say "     archived — settings are read-only, skipping"
    continue
  fi
  if printf '%s' "${state}" | grep -q '"private": true'; then
    # The card would upload fine; nothing outside GitHub could ever fetch it.
    say "     private — a social preview is never served for a private repository, skipping"
    continue
  fi

  if [ "${REMOVE}" -eq 1 ]; then
    step "Removing the uploaded card"
    say "     $(remove_card "${owner}" "${name}")"
    og_now="$(curl -sS "https://github.com/${owner}/${name}" | grep -oE 'https://repository-images\.githubusercontent\.com/[^"]+' | head -1 || true)"
    if [ -z "${og_now}" ]; then
      say "     back to GitHub's generated card"
    else
      say "     still set: ${og_now}"
      FAILED=$((FAILED + 1))
    fi
    continue
  fi

  step "Drawing the card"
  screenshot_card "${WORK_DIR}/card.html" "${IMAGES_DIR}/$(image_name "${name}").png"
  say "     $(assert_png "${IMAGES_DIR}/$(image_name "${name}").png") -> ${IMAGES_DIR}/$(image_name "${name}").png"

  if [ "${RENDER_ONLY}" -eq 1 ]; then
    continue
  fi

  step "Uploading"
  if ! upload_card "${owner}" "${name}" "${IMAGES_DIR}/$(image_name "${name}").png" >/dev/null; then
    say "     FAILED after 3 attempts"
    FAILED=$((FAILED + 1))
    continue
  fi

  step "Verifying"
  # A non-serving card is a HANDLED outcome, not an abort: silence the ERR trap across it so the
  # run does not print the generic "ABORTED" banner on top of the specific explanation below.
  trap - ERR
  set +e
  verify_card "${owner}" "${name}"
  verdict=$?
  set -e

  if [ "${verdict}" -eq 2 ] && [ "${FORCE}" -eq 0 ]; then
    # The card is set and unservable, which is worse than the generated card it replaced. Put
    # that repository back before touching any of the others: the first repository in the run is
    # the canary, and one broken card is a bug where six is an outage of your own making.
    say "     reverting, and stopping before the remaining repositories"
    remove_card "${owner}" "${name}" >/dev/null 2>&1 || true
    printf '\n!! GitHub accepted the upload but is not publishing it.\n' >&2
    printf '   %s/%s has been put back on GitHub'"'"'s generated card.\n' "${owner}" "${name}" >&2
    printf '   This is the known pipeline outage — see social-preview/README.md. If the pipeline\n' >&2
    printf '   is merely slow, raise the bound with --serve-timeout <seconds> and re-run.\n' >&2
    printf '   --force uploads without this check and without reverting.\n' >&2
    exit 1
  fi
  [ "${verdict}" -eq 0 ] || FAILED=$((FAILED + 1))
  trap 'on_err ${LINENO}' ERR
done

say ""
if [ "${FAILED}" -gt 0 ]; then
  die "${FAILED} repository(ies) did not complete."
fi
say "==> Done."
