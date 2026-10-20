#!/usr/bin/env bash
# One command to push local changes live.
#
#   ./deploy/redeploy.sh                     # test, sync, install, restart, verify
#   ./deploy/redeploy.sh --skip-tests        # when you have just run them
#   ./deploy/redeploy.sh --full              # the full bootstrap.sh even if only code changed
#   ./deploy/redeploy.sh --no-restore        # leave the server's neuron table empty
#   ./deploy/redeploy.sh --restore-jobs 8    # re-load that many cells at a time (default 4)
#   ./deploy/redeploy.sh ubuntu@1.2.3.4 ~/.ssh/key
#
# What it does, in order:
#
#   1. Runs the local test suite, ruff (src and tests) and mypy: CLAUDE.md
#      ground rule 4.  A redeploy that ships a failing build is worse than no
#      redeploy.  With pytest-xdist installed (the dev extra) the suite runs in
#      SBFSEM_TEST_WORKERS processes (default 6): about 68 s on the M4 Max on
#      2026-10-16, against 3 min 45 s serial.
#   2. Snapshots which neurons the server has loaded.
#   3. Syncs the tree and runs bootstrap.sh --quick, which reinstalls only the
#      package and restarts when the dependencies, the unit, the nginx configs and
#      bootstrap.sh are what its last full run set up, and does the full run
#      (system packages, dependencies, directories, unit, nginx) when not.
#   4. Waits for health, then re-loads the snapshotted neurons, 4 at a time, so
#      the first request after a deploy is not a cold 14s Viking fetch.  Open
#      browsers restore their own cells either way; --no-restore skips it.
#   5. Confirms the build actually changed, so a silent no-op deploy is
#      reported as one, and prints how long each step took.
#
# Open browsers notice the new build within ~20s (they poll /api/build), save
# their session, and offer a Reload that restores it.  No client action is
# needed here.

set -euo pipefail

HOST=${SBFSEM_HOST:-ubuntu@<server-ip>}
KEY=${SBFSEM_KEY:-$HOME/.ssh/sbfsem-lightsail}
REMOTE_DIR=/srv/sbfsem/app
SKIP_TESTS=0
RESTORE=1
RESTORE_JOBS=4
FULL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-tests) SKIP_TESTS=1 ;;
        --no-restore) RESTORE=0 ;;
        --restore-jobs)
            RESTORE_JOBS=${2:-}
            [[ $RESTORE_JOBS =~ ^[1-9][0-9]*$ ]] || { echo "--restore-jobs needs a count" >&2; exit 2; }
            shift ;;
        --full) FULL=1 ;;
        --warm) echo "--warm was removed: it built the default cylinder through GET /mesh, which the viewer no longer asks for (it renders through mesh jobs, with the settings it was given), so nothing it built was ever used." >&2; exit 2 ;;
        -h|--help) sed -n '2,31p' "$0"; exit 0 ;;
        *@*) HOST=$1 ;;
        *) KEY=$1 ;;
    esac
    shift
done

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# Every API call this script makes goes over SSH to the application port, not
# through the public URL.  Going through nginx would mean coping with the
# HTTP->HTTPS redirect (which turns a POST into a dropped request and makes
# `curl -f` report an empty 301 as success), and with port 443 being closed to
# the outside until the firewall is opened.  The app port needs none of that.
APP_URL=http://127.0.0.1:8765
PUBLIC_URL=${SBFSEM_URL:-https://sbfsem-tools.com}
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
STATE_DIR=$(mktemp -d)
trap 'rm -rf "$STATE_DIR"' EXIT

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }

# How long each step took, printed at the end: the numbers to compare when the
# deploy is made faster or slower.
TIMES=()
LAP=$SECONDS
START=$SECONDS
lap() { TIMES+=("$(printf '%-9s %4d s' "$1" $((SECONDS - LAP)))"); LAP=$SECONDS; }

[[ -f $KEY ]] || { echo "No SSH key at $KEY" >&2; exit 1; }

# --- 1. preflight ------------------------------------------------------------
if [[ $SKIP_TESTS -eq 0 ]]; then
    step "Testing locally before touching the server"
    VENV="$REPO_ROOT/.venv/bin"
    # CLAUDE.md ground rule 4: pytest, ruff on src and tests, and mypy, all
    # green before a deploy.  Without the venv none of them can run, and
    # shipping on a machine that cannot test (the Windows VM, a fresh clone)
    # is exactly what the rule is there to stop; --skip-tests says so out loud.
    if [[ ! -x $VENV/python ]]; then
        echo "No .venv at $VENV -- cannot test, so not deploying (--skip-tests ships anyway)." >&2
        exit 1
    fi
    PYTEST=(-q "$REPO_ROOT/tests" -x --no-header)
    # In parallel when pytest-xdist is there (the dev extra): each worker has its
    # own cache and save directories (tests/conftest.py).  About 1.2 GB a worker.
    if "$VENV/python" -c "import xdist" 2>/dev/null; then
        PYTEST+=(-n "${SBFSEM_TEST_WORKERS:-6}")
    fi
    "$VENV/python" -m pytest "${PYTEST[@]}" \
        || { echo "Tests failed -- not deploying." >&2; exit 1; }
    "$VENV/python" -m ruff check "$REPO_ROOT/src" "$REPO_ROOT/tests" \
        || { echo "Lint failed -- not deploying." >&2; exit 1; }
    (cd "$REPO_ROOT" && "$VENV/python" -m mypy src) \
        || { echo "Type check failed -- not deploying." >&2; exit 1; }
    lap checks
fi

# --- 2. snapshot server-side session ----------------------------------------
# The service holds its loaded neurons in memory only, so a restart empties it.
# Clients restore themselves from localStorage, but re-loading here means the
# first one back does not wait on Viking.
step "Snapshotting the server's loaded neurons"
if "${SSH[@]}" "curl -fsS --max-time 20 $APP_URL/api/neurons" > "$STATE_DIR/neurons.json" 2>/dev/null \
    && [[ -s "$STATE_DIR/neurons.json" ]]; then
    LOADED=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' \
        "$STATE_DIR/neurons.json" 2>/dev/null || echo 0)
    echo "    $LOADED neuron(s) loaded"
else
    echo "    server not reachable (first deploy, or it is down) -- nothing to snapshot"
    echo '[]' > "$STATE_DIR/neurons.json"
    LOADED=0
fi

BEFORE=$("${SSH[@]}" "curl -fsS --max-time 10 $APP_URL/api/build" 2>/dev/null || echo '{}')
lap snapshot

# --- 3. sync -----------------------------------------------------------------
step "Syncing $REPO_ROOT -> $HOST:$REMOTE_DIR"
"$REPO_ROOT/deploy/sync.sh" "$HOST" "$KEY"
lap sync

# --- 4. install and restart --------------------------------------------------
# bootstrap.sh decides on the server, against the tree just synced: --quick
# reinstalls the package alone only when its install stamp matches, so a change
# to the dependencies, the unit or the nginx configs (a new directory, ground
# rule 10) still gets the full run.
if [[ $FULL -eq 1 ]]; then
    step "Installing and restarting on the server (full bootstrap, as asked)"
    "${SSH[@]}" "sudo bash $REMOTE_DIR/deploy/bootstrap.sh"
else
    step "Installing and restarting on the server"
    "${SSH[@]}" "sudo bash $REMOTE_DIR/deploy/bootstrap.sh --quick"
fi
lap install

# --- 5. wait for health ------------------------------------------------------
# bootstrap.sh has already waited; this is the same check from here, one try in
# the normal case.
step "Waiting for the service"
for attempt in $(seq 1 30); do
    if "${SSH[@]}" "curl -fsS --max-time 5 $APP_URL/api/health" >/dev/null 2>&1; then
        break
    fi
    [[ $attempt -eq 30 ]] && { echo "Service did not come back." >&2; exit 1; }
    sleep 2
done

AFTER=$("${SSH[@]}" "curl -fsS --max-time 10 $APP_URL/api/build")

# One helper rather than an f-string per field: nesting quotes inside a
# python3 -c inside a shell string is how the previous version broke.
field() { echo "$1" | python3 -c 'import json,sys;print(json.load(sys.stdin).get(sys.argv[1],""))' "$2" 2>/dev/null || echo ''; }

after_assets=$(field "$AFTER" assets)
before_assets=$(field "$BEFORE" assets)
echo "    version $(field "$AFTER" version)  assets $after_assets  instance $(field "$AFTER" instance)"

# A deploy that changes nothing is usually a sync that silently did nothing.
if [[ -n $before_assets && $before_assets == "$after_assets" ]]; then
    warn "assets unchanged ($after_assets) -- no frontend change was shipped"
fi

lap health

# --- 6. restore the neuron table ---------------------------------------------
# The snapshot is copied to the server and replayed there against the app port,
# so this works regardless of TLS, redirects or whether 443 is open.  Several at
# a time, because a cell whose section correction Viking must be asked for again
# waits seconds on it (its correction grid changed: 343 cells one by one took
# 604 s at 2.6.3; 57 such cells among 342 took 167 s four at a time at 2.6.4),
# and the service loads different cells side by side (its gate is per cell; the
# Viking round trip runs outside the lock).  From the disk cache a load is Python
# work in the one service process, so there 4 at a time is a little slower than
# one by one (342 cells: 67.1 s against 51.1 s, 2026-10-16).
if [[ $RESTORE -eq 1 && ${LOADED:-0} -gt 0 ]]; then
    step "Re-loading $LOADED neuron(s), $RESTORE_JOBS at a time"
    scp -q -i "$KEY" "$STATE_DIR/neurons.json" "$HOST:/tmp/sbfsem-restore.json"
    "${SSH[@]}" "APP_URL=$APP_URL JOBS=$RESTORE_JOBS python3 - /tmp/sbfsem-restore.json" <<'PY'
import json, os, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

base = os.environ["APP_URL"]


def reload(entry):
    # Only the neuron table is being warmed: the answer's "edited in Viking"
    # check is thrown away, so it is not asked for (two Viking requests a
    # cell, 335 cells on 2026-10-15).
    body = json.dumps({
        "id": entry["id"], "source": entry["source"], "include_synapses": True,
        "check_fresh": False,
    }).encode()
    req = urllib.request.Request(
        base + "/api/neurons", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            r.read()
        print("    c{}  {:.2f}s".format(entry["id"], time.perf_counter() - start), flush=True)
        return True
    except (urllib.error.URLError, OSError) as exc:
        # A neuron that will not reload is not a reason to fail the deploy;
        # the client that wants it will retry on its own.
        print("    c{}  FAILED: {}".format(entry["id"], exc), file=sys.stderr, flush=True)
        return False


with open(sys.argv[1], encoding="utf-8") as fh:
    entries = json.load(fh)
start = time.perf_counter()
with ThreadPoolExecutor(max_workers=int(os.environ["JOBS"])) as pool:
    done = list(pool.map(reload, entries))
print("    {} of {} re-loaded in {:.1f} s".format(sum(done), len(done), time.perf_counter() - start))
PY
    "${SSH[@]}" "rm -f /tmp/sbfsem-restore.json"
    lap restore
fi

step "Live at $PUBLIC_URL"
echo "    Open browsers will offer a reload within 20s and restore their session."
echo "    Took $((SECONDS - START)) s:"
for line in "${TIMES[@]}"; do echo "      $line"; done
