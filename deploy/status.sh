#!/usr/bin/env bash
# Production health at a glance -- read-only.
#
#   ./deploy/status.sh                  # the report
#   ./deploy/status.sh --lines 200      # more journal lines (default 60)
#   ./deploy/status.sh --since -2h      # journal window (default -24h)
#   ./deploy/status.sh --errors         # only journal lines that look like trouble
#   ./deploy/status.sh --stack          # add a py-spy dump: what the service is doing now
#   ./deploy/status.sh ubuntu@1.2.3.4 ~/.ssh/key
#
# Sections, in the order a diagnosis usually needs them:
#
#   1. Build      what is deployed (/api/build) and whether it answers (/api/health)
#   2. Service    active?, restarts, memory vs its cgroup ceilings, memory PRESSURE
#                 (any nonzero avg10 means the kernel is reclaim-stalling it --
#                 the 2026-09-01 "running for 30 min at 0 % CPU" signature)
#   3. Box        load, RAM, disk under /srv
#   4. Loaded     the neurons the service holds in memory
#   5. Journal    the service log (uvicorn + the app's own logging)
#   6. nginx      error log tail, status-code histogram of the last 2,000
#                 requests, and the last non-2xx/3xx requests (a run of 422s
#                 is a client sending an out-of-range knob; 502/504 is a build
#                 outrunning nginx's 300 s)
#   7. Stack      (--stack) py-spy dump of the worker, for a build that seems hung
#
# Nothing here restarts, edits or deletes anything.

set -euo pipefail

HOST=${SBFSEM_HOST:-ubuntu@<server-ip>}
KEY=${SBFSEM_KEY:-$HOME/.ssh/sbfsem-lightsail}
LINES=60
SINCE=-24h
ERRORS=0
STACK=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --lines) LINES=$2; shift ;;
        --since) SINCE=$2; shift ;;
        --errors) ERRORS=1 ;;
        --stack) STACK=1 ;;
        -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
        *@*) HOST=$1 ;;
        *) KEY=$1 ;;
    esac
    shift
done

[[ -f $KEY ]] || { echo "No SSH key at $KEY" >&2; exit 1; }

ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST" \
    "LINES=$LINES SINCE=$(printf %q "$SINCE") ERRORS=$ERRORS STACK=$STACK bash -s" <<'REMOTE'
set -uo pipefail
APP=http://127.0.0.1:8765
UNIT=sbfsem-web
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
mb() { awk -v b="$1" 'BEGIN { if (b ~ /^[0-9]+$/) printf "%d MB", b / 1048576; else print b }'; }

step "Build ($(hostname), $(date -u +%Y-%m-%dT%H:%M:%SZ))"
curl -fsS --max-time 5 "$APP/api/build" 2>/dev/null || echo "    /api/build: NOT ANSWERING"
echo
curl -fsS --max-time 5 "$APP/api/health" 2>/dev/null || echo "    /api/health: NOT ANSWERING"
echo

step "Service"
printf '    state      %s (since %s)\n' "$(systemctl is-active $UNIT)" \
    "$(systemctl show $UNIT -p ActiveEnterTimestamp --value)"
printf '    restarts   %s   main pid %s\n' "$(systemctl show $UNIT -p NRestarts --value)" \
    "$(systemctl show $UNIT -p MainPID --value)"
printf '    memory     %s now / high %s / max %s\n' \
    "$(mb "$(systemctl show $UNIT -p MemoryCurrent --value)")" \
    "$(mb "$(systemctl show $UNIT -p MemoryHigh --value)")" \
    "$(mb "$(systemctl show $UNIT -p MemoryMax --value)")"
PRESSURE=/sys/fs/cgroup/system.slice/$UNIT.service/memory.pressure
if [[ -r $PRESSURE ]]; then
    sed 's/^/    pressure   /' "$PRESSURE"
else
    echo "    pressure   (cgroup file not readable)"
fi
MAINPID=$(systemctl show $UNIT -p MainPID --value)
if [[ ${MAINPID:-0} -gt 0 ]]; then
    printf '    worker     '; ps -o pid=,pcpu=,rss=,etime= -p "$MAINPID" \
        | awk '{ printf "pid %s  cpu %s%%  rss %d MB  up %s\n", $1, $2, $3/1024, $4 }'
fi

step "Box"
printf '    %s\n' "$(uptime)"
free -m | awk 'NR==2 { printf "    RAM        %d MB used / %d MB total, %d MB available\n", $3, $2, $7 }'
df -h /srv | awk 'NR==2 { printf "    disk /srv  %s used of %s (%s)\n", $3, $2, $5 }'

step "Loaded neurons"
curl -fsS --max-time 10 "$APP/api/neurons" 2>/dev/null | python3 -c "
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    print('    (no answer)'); sys.exit()
print(f'    {len(rows)} loaded')
for r in rows:
    label = r.get('label') or ''
    print(f\"    c{r['id']:<7} {r.get('source', ''):<22} {r.get('n_nodes', 0):>7,} nodes  {label}\")
" || echo "    (no answer)"

step "Journal (since $SINCE, last $LINES lines$( [[ $ERRORS -eq 1 ]] && echo ', trouble only'))"
if [[ $ERRORS -eq 1 ]]; then
    sudo -n journalctl -u $UNIT --since "$SINCE" --no-pager -o short-iso \
        | grep -iE 'error|traceback|exception|warning|critical|killed|oom|timeout|failed' \
        | tail -n "$LINES" || echo "    (nothing that looks like trouble)"
else
    sudo -n journalctl -u $UNIT --since "$SINCE" -n "$LINES" --no-pager -o short-iso
fi

step "nginx error log (last 20)"
sudo -n tail -n 20 /var/log/nginx/sbfsem.error.log 2>/dev/null || echo "    (none)"

step "nginx status codes (last 2,000 requests)"
sudo -n tail -n 2000 /var/log/nginx/sbfsem.access.log 2>/dev/null \
    | awk '{ print $9 }' | sort | uniq -c | sort -rn | awk '{ printf "    %6d  %s\n", $1, $2 }'
echo "    last non-2xx/3xx requests:"
sudo -n tail -n 2000 /var/log/nginx/sbfsem.access.log 2>/dev/null \
    | awk '$9 >= 400 { print }' | tail -n 15 | sed 's/^/    /' || true

if [[ $STACK -eq 1 ]]; then
    step "py-spy dump of the worker"
    if [[ -x /srv/sbfsem/venv/bin/py-spy && ${MAINPID:-0} -gt 0 ]]; then
        sudo -n /srv/sbfsem/venv/bin/py-spy dump --pid "$MAINPID" 2>&1 | head -n 80
    else
        echo "    py-spy not installed: sudo /srv/sbfsem/venv/bin/pip install py-spy"
    fi
fi
echo
REMOTE
