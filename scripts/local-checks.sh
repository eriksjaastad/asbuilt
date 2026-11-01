#!/usr/bin/env bash
# Local checks for asbuilt: the Python tests, then the page's JavaScript tests.
#
# Run by a shared local-checks runner that checks the commit out in a fresh
# worktree and sets LOCAL_CHECKS_CLEAN and LOCAL_CHECKS_SHA. It runs on every
# push; nothing else runs these checks, since this repository has no CI. To run the
# two suites by hand: uv run python -m unittest discover -s tests, then npm ci and
# node --test tests/*.test.js.
#
# The page's tests need elkjs, roughjs and playwright-core from node_modules, and the
# browser test needs Playwright's Chromium for that version, installed once with
# npx playwright-core install chromium-headless-shell. The packages are installed
# with npm ci from the local npm cache only: exactly the locked packages, no
# network, no install scripts, and no git dependencies (git is set to false).
set -euo pipefail

if [ "${LOCAL_CHECKS_CLEAN:-}" != 1 ]; then
    echo "local checks: run them through the local-checks runner, which checks HEAD in a fresh worktree" >&2
    exit 2
fi
cd "$(git rev-parse --show-toplevel)"
sha="$(git rev-parse HEAD)"
if [ "$sha" != "$(git rev-parse "${LOCAL_CHECKS_SHA}^{commit}")" ]; then
    echo "local checks: this checkout is at $sha, not $LOCAL_CHECKS_SHA" >&2
    exit 2
fi

# Run one named check and return its status; a failure is recorded, so one run reports every failure.
failed=()
step() {
    local name="$1" status=0
    shift
    echo "== $name"
    "$@" || status=$?
    [ "$status" -eq 0 ] || failed+=("$name")
    return "$status"
}

step "python tests" uv run -q python -m unittest discover -s tests || true
if step "page packages (npm ci, offline, no scripts)" \
    npm ci --offline --ignore-scripts --git=false --no-audit --no-fund; then
    step "page tests" node --test tests/*.test.js
fi

if [ "${#failed[@]}" -gt 0 ]; then
    printf 'local checks FAILED: %s\n' "${failed[@]}"
    exit 1
fi
echo "local checks passed"
