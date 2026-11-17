#!/usr/bin/env bash
set -euo pipefail

repo=/opt/discord-bot-chat
repo_owner=ubuntu
service=discord-bot.service
runtime=/run/discord-bot-deploy
state=/var/lib/discord-bot-deploy
status_url=http://127.0.0.1:8765/internal/gomoku/deploy-status
asset_url=http://127.0.0.1:8765/gomoku/assets/room.js
api=https://api.github.com/repos/Axicavlo/DiscordBotChatServer/actions/workflows/ci.yml/runs
bootstrap=false
if [[ ${1:-} == --bootstrap && $# == 1 ]]; then
    bootstrap=true
elif [[ $# != 0 ]]; then
    echo "Usage: $0 [--bootstrap]" >&2
    exit 2
fi

git_as_owner() {
    runuser -u "$repo_owner" -- git -C "$repo" "$@"
}

install -d -m 0755 "$runtime"
install -d -m 0700 "$state"
exec 9>"$runtime/lock"
flock -n 9 || exit 0
# Clear a flag left by a previous process that was killed ungracefully.
rm -f "$runtime/drain"

[[ $(git_as_owner symbolic-ref --short HEAD) == main ]]
[[ -z $(git_as_owner status --porcelain --untracked-files=no) ]]
git_as_owner fetch --quiet origin main
previous=$(git_as_owner rev-parse HEAD)
target=$(git_as_owner rev-parse origin/main)
if [[ $target == "$previous" ]]; then
    echo "Already deployed: $target"
    exit 0
fi
if [[ -f $state/failed-sha && $(cat "$state/failed-sha") == "$target" ]]; then
    echo "Skipping previously failed deployment: $target"
    exit 0
fi
if ! git_as_owner merge-base --is-ancestor "$previous" "$target"; then
    echo "Deployment requires a fast-forward from $previous to $target" >&2
    exit 1
fi
if ! git_as_owner diff --quiet "$previous" "$target" -- db/ requirements.txt; then
    echo "Dependency or DB schema changes require manual deployment: $target"
    exit 0
fi

runs=$(curl -fsS --max-time 15 --retry 1 -H 'Accept: application/vnd.github+json'     "$api?head_sha=$target&branch=main&event=push&per_page=1")
if ! python3 -c '
import json, sys
runs = json.load(sys.stdin)["workflow_runs"]
sys.exit(not any(
    run["head_sha"] == sys.argv[1] and run["head_branch"] == "main"
    and run["event"] == "push" and run["conclusion"] == "success"
    for run in runs
))
' "$target" <<< "$runs"; then
    echo "Waiting for successful CI: $target"
    exit 0
fi

trap 'rm -f "$runtime/drain"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
touch "$runtime/drain"

if status=$(curl -fsS --max-time 3 "$status_url"); then
    if ! python3 -c '
import json, sys
state = json.load(sys.stdin)
sys.exit(not (state["draining"] is True and state["rooms"] == 0))
' <<< "$status"; then
        echo "Deferring deployment while Gomoku rooms are open"
        exit 0
    fi
elif ! "$bootstrap"; then
    echo "Room state unavailable; deployment skipped" >&2
    exit 1
fi

healthy() {
    local attempt response
    for attempt in {1..16}; do
        if systemctl is-active --quiet "$service" &&
           response=$(curl -fsS --max-time 2 "$status_url" 2>/dev/null) &&
           python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("ready") is not True)' <<< "$response" &&
           curl -fsS --max-time 2 -o /dev/null "$asset_url" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

git_as_owner merge --quiet --ff-only "$target"
if systemctl restart "$service" && healthy; then
    rm -f "$state/failed-sha"
    echo "Deployed $target"
    exit 0
fi

echo "New release failed health checks; restoring $previous" >&2
printf '%s\n' "$target" > "$state/failed-sha"
git_as_owner reset --keep "$previous"
systemctl restart "$service"
systemctl is-active --quiet "$service"
exit 1
