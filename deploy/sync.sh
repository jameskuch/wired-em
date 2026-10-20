#!/usr/bin/env bash
# Copy the application tree to the Lightsail instance.
#
# Run LOCALLY from the repository root:
#
#   ./deploy/sync.sh ubuntu@203.0.113.10
#   ./deploy/sync.sh ubuntu@203.0.113.10 ~/.ssh/sbfsem-lightsail
#
# rsync only sends what changed, so re-running after an edit takes a second.

set -euo pipefail

TARGET=${1:?"usage: $0 user@host [ssh-key] "}
KEY=${2:-$HOME/.ssh/sbfsem-lightsail}
REMOTE_DIR=/srv/sbfsem/app
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [[ ! -f "$KEY" ]]; then
    echo "No SSH key at $KEY -- pass one as the second argument." >&2
    exit 1
fi

# macOS ships openrsync, which reports itself as "rsync version 2.6.9
# compatible" and rejects --info=. Detect it rather than requiring a Homebrew
# rsync just to deploy.
if rsync --info=stats1 --version >/dev/null 2>&1; then
    REPORT=(--info=stats1,progress2)
else
    # --progress on the old client is per-file and deafening across 350 files;
    # --stats alone gives the summary that actually matters.
    REPORT=(--stats)
fi

echo "==> Syncing $REPO_ROOT -> $TARGET:$REMOTE_DIR"

# The remote user cannot write /srv directly, so create the app directory and
# hand it over. Only the app directory -- /srv/sbfsem/cache belongs to the
# service account, and chowning it here would stop the service writing to it.
ssh -i "$KEY" "$TARGET" \
    "sudo mkdir -p $REMOTE_DIR && sudo chown -R \$USER:\$USER $REMOTE_DIR"

rsync -az --delete "${REPORT[@]}" \
    -e "ssh -i $KEY" \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache/' \
    --exclude '.ruff_cache/' \
    --exclude '.mypy_cache/' \
    --exclude '.git/' \
    --exclude 'build/' \
    --exclude 'dist/' \
    --exclude '*.egg-info/' \
    --exclude '.DS_Store' \
    --exclude '.coverage' \
    --exclude '.claude/' \
    "$REPO_ROOT/" "$TARGET:$REMOTE_DIR/"

echo
echo "==> Synced. On the server, run:"
echo "    sudo bash $REMOTE_DIR/deploy/bootstrap.sh"
