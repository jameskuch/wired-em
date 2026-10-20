#!/usr/bin/env bash
# What development runs leave running on this Mac, and a way to stop it.
#
#   tools/cleanup_dev.sh          # list only: nothing is stopped
#   tools/cleanup_dev.sh --kill   # stop them (SIGTERM, SIGKILL after 5 s) and delete orphaned profiles
#
# It looks for:
#   - headless Chrome for Testing: the browser checks (tools/check_*.py) start it, and close
#     it on any exit they get to see; a check killed with SIGKILL, or a script of a session's
#     own, can still leave one behind;
#   - the throwaway profiles those checks make in $TMPDIR (check-dropdowns-*) with no
#     browser using them any more;
#   - uvicorn servers listening on ports 8765-8799 (the dev servers of .claude/launch.json
#     and of sessions that picked a free port).  Anything else listening there is shown
#     and left alone.
#
# Several sessions share this machine: run it without --kill first, and check the
# working directory column before stopping a server another session may still be using.

set -euo pipefail

KILL=0
case "${1:-}" in
    --kill) KILL=1 ;;
    "") ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "usage: $0 [--kill]" >&2; exit 2 ;;
esac

PORTS=8765-8799
TMP=${TMPDIR:-/tmp}
TMP=${TMP%/}

describe() {   # pid -> "elapsed  rss MB  cwd"
    local pid=$1 etime rss cwd
    etime=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ') || true
    rss=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ') || true
    cwd=$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1) || true
    printf '%-12s %6s MB  %s' "${etime:-?}" "$(( ${rss:-0} / 1024 ))" "${cwd:-?}"
}

stop() {   # pids...: SIGTERM, then SIGKILL whatever is left after 5 s
    [[ $# -eq 0 ]] && return 0
    kill -TERM "$@" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        local alive=0 pid
        for pid in "$@"; do kill -0 "$pid" 2>/dev/null && alive=1; done
        [[ $alive -eq 0 ]] && return 0
        sleep 0.5
    done
    kill -KILL "$@" 2>/dev/null || true
}

# --- Chrome for Testing ------------------------------------------------------------
# The browser processes only, not their helpers (renderer, GPU, network), which go
# with them.  Chrome for Testing is a test-only browser: nobody browses with it.
chrome_pids=()
chrome_lines=()
while read -r pid command; do
    [[ -z ${pid:-} ]] && continue
    case "$command" in
        *Helper*|*--type=*) continue ;;
    esac
    profile=$(printf '%s\n' "$command" | sed -n 's/.*--user-data-dir=\([^ ]*\).*/\1/p')
    chrome_pids+=("$pid")
    chrome_lines+=("$(printf '%7s  %s  %s' "$pid" "$(describe "$pid")" "${profile:-no profile}")")
done < <(ps -axo pid=,command= | grep -E 'Google Chrome for Testing|chrome-headless-shell|ms-playwright/chromium' | grep -v grep || true)

echo "Chrome for Testing: ${#chrome_pids[@]} browser(s)"
for line in "${chrome_lines[@]+"${chrome_lines[@]}"}"; do echo "$line"; done

# --- profiles no browser uses ------------------------------------------------------
orphans=()
for dir in "$TMP"/check-dropdowns-*; do
    [[ -d $dir ]] || continue
    if ! ps -axo command= | grep -F -- "--user-data-dir=$dir" | grep -vq grep; then
        orphans+=("$dir")
    fi
done
echo
echo "Orphaned check profiles in $TMP: ${#orphans[@]}"
if [[ ${#orphans[@]} -gt 0 ]]; then
    du -sh "${orphans[@]}" 2>/dev/null | sed 's/^/    /' | head -20
    [[ ${#orphans[@]} -gt 20 ]] && echo "    ... and $(( ${#orphans[@]} - 20 )) more"
fi

# --- dev servers -------------------------------------------------------------------
server_pids=()
echo
echo "Listening on $PORTS:"
listeners=$(lsof -nP -iTCP:"$PORTS" -sTCP:LISTEN -Fpn 2>/dev/null || true)
seen=" "
pid=""
while read -r field; do
    case "$field" in
        p*) pid=${field#p} ;;
        n*)
            port=${field##*:}
            [[ $seen == *" $pid "* ]] && continue
            seen="$seen$pid "
            command=$(ps -o command= -p "$pid" 2>/dev/null || true)
            if [[ $command == *uvicorn* || $command == *sbfsem_web* || $command == *sbfsem-web* ]]; then
                server_pids+=("$pid")
                printf '%7s  :%s  %s\n         %s\n' "$pid" "$port" "$(describe "$pid")" "${command:0:160}"
            else
                printf '%7s  :%s  not a uvicorn server, left alone: %s\n' "$pid" "$port" "${command:0:120}"
            fi
            ;;
    esac
done <<< "$listeners"
[[ ${#server_pids[@]} -eq 0 ]] && echo "    no uvicorn server"

if [[ $KILL -eq 0 ]]; then
    echo
    echo "Nothing was stopped.  --kill stops the ${#chrome_pids[@]} browser(s) and ${#server_pids[@]} server(s)" \
         "above and deletes the ${#orphans[@]} orphaned profile(s)."
    exit 0
fi

echo
stop "${chrome_pids[@]+"${chrome_pids[@]}"}" "${server_pids[@]+"${server_pids[@]}"}"
# A killed browser's profile is now an orphan too.
for dir in "$TMP"/check-dropdowns-*; do
    [[ -d $dir ]] || continue
    if ! ps -axo command= | grep -F -- "--user-data-dir=$dir" | grep -vq grep; then
        rm -rf -- "${dir:?}"
    fi
done
echo "Stopped ${#chrome_pids[@]} browser(s) and ${#server_pids[@]} server(s); orphaned profiles deleted."
