#!/bin/bash
# Tests the "Decide whether macOS runs" step of .github/workflows/gate.yml, run as written: the
# lines between `# scope:begin` and `# scope:end` are cut out of the workflow and run against
# throwaway git repositories.
#
#   scripts/test-gate-scope.sh            run the cases
#   scripts/test-gate-scope.sh --mutate   also check that each planted defect fails a case
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
workflow="$here/.github/workflows/gate.yml"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

extract() {
  awk '/# scope:begin/{f=1; next} /# scope:end/{f=0} f' "$workflow" | sed 's/^          //'
}

# repo <dir> <base files...> -- <changed files...>: a base commit, then one commit on top, so
# HEAD^1..HEAD is the change set, as on GitHub's merge commit.
repo() {
  local dir=$1; shift
  rm -rf "$dir"; mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" config user.email t@example.com
  git -C "$dir" config user.name t
  while [ "$1" != "--" ]; do mkdir -p "$dir/$(dirname "$1")"; echo base > "$dir/$1"; shift; done
  shift
  git -C "$dir" add -A; git -C "$dir" commit -qm base --allow-empty
  for f in "$@"; do mkdir -p "$dir/$(dirname "$f")"; echo change >> "$dir/$f"; done
  git -C "$dir" add -A; git -C "$dir" commit -qm change --allow-empty
}

pass=0; fail=0
# check <name> <script> <dir> <event> <scope> <want>
check() {
  local name=$1 script=$2 dir=$3 event=$4 scope=$5 want=$6 out got
  : > "$tmp/out"
  out=$(cd "$dir" && EVENT=$event MAC_SCOPE=$scope GITHUB_OUTPUT="$tmp/out" bash "$script" 2>&1) || true
  got=$(sed -n 's/^mac=//p' "$tmp/out")
  if [ "$got" = "$want" ]; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1)); echo "FAIL $name: want mac=$want, got mac=${got:-<none>} ($out)"
  fi
}

run_cases() {
  local s=$1 r=$tmp/r
  repo "$r" a.md skills/orchestrator/orch.py -- skills/orchestrator/scripts/test_orch.sh
  check "schedule is a full pass"            "$s" "$r" schedule          ""                        true
  check "manual run is a full pass"          "$s" "$r" workflow_dispatch ""                        true
  check "empty scope never runs on a PR"     "$s" "$r" pull_request      ""                        false
  check "blank scope counts as empty"        "$s" "$r" pull_request      "
  "                                                                                              false
  check "* crosses /"                        "$s" "$r" pull_request      "skills/orchestrator/*"   true
  check "a scope that matches nothing"       "$s" "$r" pull_request      "bin/* fish/*"            false
  check "the second of two lines matches"    "$s" "$r" pull_request      "bin/*
skills/*"                                                                                        true

  repo "$r" a.md -- b.md
  check "a glob is not expanded in the tree" "$s" "$r" pull_request      "*.md"                    true
  repo "$r" a.md -- x.sh
  check "*.md does not match x.sh"           "$s" "$r" pull_request      "*.md"                    false
  repo "$r" -- "dir with space/f.sh"
  check "a path with a space"                "$s" "$r" pull_request      "dir?with?space/*"        true

  repo "$r" a.md --
  check "an empty change set matches nothing" "$s" "$r" pull_request     "*"                       false

  rm -rf "$r"; mkdir -p "$r"; git -C "$r" init -q
  git -C "$r" -c user.email=t@example.com -c user.name=t commit -qm only --allow-empty
  check "no parent fails toward running"     "$s" "$r" pull_request      "bin/*"                   true
}

extract > "$tmp/scope.sh"
[ -s "$tmp/scope.sh" ] || { echo "FAIL: no scope:begin/scope:end block in $workflow"; exit 1; }
run_cases "$tmp/scope.sh"
echo "$pass passed, $fail failed"
base_fail=$fail

if [ "${1:-}" = "--mutate" ]; then
  # Each mutant is one sed expression on the step; a mutant that passes every case is a gap.
  # shellcheck disable=SC2016 # the $ are sed text, not expansions
  mutants=(
    's/^set -f.*//'
    's/mac=true; why="could not list/mac=false; why="could not list/'
    's/schedule|workflow_dispatch)/schedule)/'
    's/\[\[ \$f == \$g \]\]/[[ $f == "$g" ]]/'
    's/\[ -n "\$f" \] || continue//'
    's/HEAD\^1 HEAD/HEAD/'
  )
  caught=0
  for m in "${mutants[@]}"; do
    sed "$m" "$tmp/scope.sh" > "$tmp/mutant.sh"
    if cmp -s "$tmp/scope.sh" "$tmp/mutant.sh"; then echo "MUTANT DID NOT APPLY: $m"; continue; fi
    pass=0; fail=0
    run_cases "$tmp/mutant.sh" > /dev/null
    if [ "$fail" -gt 0 ]; then caught=$((caught + 1)); else echo "MUTANT SURVIVED: $m"; fi
  done
  echo "mutants caught: $caught/${#mutants[@]}"
  [ "$caught" -eq "${#mutants[@]}" ] || exit 1
fi
[ "$base_fail" -eq 0 ]
