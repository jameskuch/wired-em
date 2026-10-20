#!/usr/bin/env bash
# Prepare a fresh Lightsail instance to run SBFSEM-pytools.
#
# Run ON THE SERVER, after the application tree has been synced to
# /srv/sbfsem/app (see deploy/sync.sh):
#
#   sudo bash /srv/sbfsem/app/deploy/bootstrap.sh            # the full run
#   sudo bash /srv/sbfsem/app/deploy/bootstrap.sh --quick    # the package alone, if nothing else changed
#
# Idempotent: safe to re-run after a code update.  --quick (what redeploy.sh asks
# for) reinstalls only the package and restarts the service when the dependencies,
# the unit, the nginx configs and this script are what the last full run set up
# (deploy/install_stamp.py), and does the full run when any of them differs.

set -euo pipefail

QUICK=0
case "${1:-}" in
    --quick) QUICK=1 ;;
    "") ;;
    *) echo "usage: $0 [--quick]" >&2; exit 2 ;;
esac

APP_DIR=/srv/sbfsem/app
VENV_DIR=/srv/sbfsem/venv
CACHE_DIR=/srv/sbfsem/cache
#: User-saved workspaces. Separate from the cache so clearing the cache
#: never takes someone's saved work with it.
SAVE_DIR=/srv/sbfsem/saves
#: users.json + session signing key. Login is on iff users.json exists;
#: maintain it with `sudo -u sbfsem $VENV_DIR/bin/sbfsem-users --dir $AUTH_DIR ...`.
AUTH_DIR=/srv/sbfsem/auth
#: Render settings attached to individual cells (the app's "Attach render").
CELL_RENDER_DIR=/srv/sbfsem/cell-renders
LIBRARY_DIR=/srv/sbfsem/library
SERVICE_USER=sbfsem
#: Which certificate to look for when deciding between the HTTP and TLS nginx
#: configs.  Override for a different domain: SITE_DOMAIN=example.org sudo -E ...
SITE_DOMAIN=${SITE_DOMAIN:-sbfsem-tools.com}

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash $0" >&2
    exit 1
fi
if [[ ! -f "$APP_DIR/pyproject.toml" ]]; then
    echo "No application at $APP_DIR -- sync the tree first." >&2
    exit 1
fi

wait_for_service() {
    # Straight to the application port, not through nginx: once TLS is on, port 80
    # answers with a 301 that `curl -f` treats as success, so going via the proxy
    # would report a healthy service even with the app down.
    local app_url=http://127.0.0.1:8765
    echo "==> Waiting for the service"
    for _ in $(seq 1 120); do
        if curl -fsS "$app_url/api/health" >/dev/null 2>&1; then
            echo
            echo "Ready: $(curl -fsS "$app_url/api/health")"
            echo "Warming the IPL boundary surfaces (~1s each, cached thereafter)..."
            curl -fsS -o /dev/null "$app_url/api/volumes" || true
            return 0
        fi
        sleep 0.5
    done
    echo "Service did not come up. Recent logs:" >&2
    journalctl -u sbfsem-web -n 40 --no-pager >&2
    return 1
}

# --- the quick path ------------------------------------------------------------
# A deploy that changes only this package's code needs none of the rest below: no
# system packages, no dependency resolution, no MeshLab proof, no new directories,
# unit or nginx config, and no nginx restart.  The stamp fingerprints everything
# else this script acts on, and a full run leaves it in the venv once the service
# is up; --quick takes the short path only when the synced tree's stamp is that
# one.  So a new directory (ground rule 10 touches the unit and this script) or a
# new dependency always gets the full run, and so does a fresh venv.
STAMP_FILE=$VENV_DIR/.sbfsem-install-stamp
STAMP=$(python3 "$APP_DIR/deploy/install_stamp.py" "$APP_DIR")
if [[ $QUICK -eq 1 ]]; then
    installed=$(cat "$STAMP_FILE" 2>/dev/null || true)
    if [[ -x $VENV_DIR/bin/python && $installed == "$STAMP" ]]; then
        echo "==> Quick: dependencies, unit, nginx configs and bootstrap unchanged -- reinstalling the package only"
        # Never from what an earlier build left behind (see the full run below).
        rm -rf "$APP_DIR/build"
        # No isolated build environment (the full run put setuptools and wheel in
        # the venv) and no dependency resolution (the stamp says they are installed).
        ( cd "$APP_DIR" && "$VENV_DIR/bin/pip" install --quiet --no-deps --force-reinstall \
            --no-build-isolation . )
        chmod -R a+rX "$VENV_DIR"
        DEPLOY_USER=$(stat -c '%U' "$APP_DIR")
        chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
        chmod -R a+rX "$APP_DIR"
        systemctl restart sbfsem-web
        wait_for_service
        exit 0
    fi
    if [[ -z $installed ]]; then
        echo "==> No install stamp from a full run yet: doing the full run"
    else
        echo "==> Dependencies, unit, nginx configs or bootstrap changed since the last full run: doing the full run"
    fi
fi

# --- distribution differences ------------------------------------------------
if [[ -f /etc/os-release ]]; then . /etc/os-release; fi
case "${ID:-}" in
    ubuntu|debian)
        PKG=apt
        NGINX_SITE=/etc/nginx/sites-available/sbfsem
        ;;
    amzn|rhel|centos|almalinux)
        PKG=dnf
        NGINX_SITE=/etc/nginx/conf.d/sbfsem.conf
        ;;
    *)
        echo "Unsupported distribution: ${ID:-unknown}" >&2
        exit 1
        ;;
esac

# This script is re-run on every deploy, so skip the package step entirely once
# the prerequisites are in place. That keeps a redeploy to a few seconds and
# sidesteps the dpkg lock, which unattended-upgrades holds often enough to have
# broken a deploy already.
have_prereqs() {
    if [[ $PKG == apt ]]; then
        dpkg -s python3-venv python3-dev build-essential nginx rsync >/dev/null 2>&1
    else
        rpm -q python3.12 python3.12-devel gcc nginx rsync >/dev/null 2>&1
    fi
}

wait_for_apt_lock() {
    for i in $(seq 1 60); do
        if ! fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; then
            return 0
        fi
        [[ $i -eq 1 ]] && echo "    waiting for the dpkg lock (unattended-upgrades?)"
        sleep 5
    done
    echo "dpkg lock still held after five minutes." >&2
    echo "Check: ps aux | grep -E 'apt|dpkg|unattended'" >&2
    return 1
}

if have_prereqs; then
    echo "==> System packages already present, skipping"
else
    echo "==> Installing system packages (${PKG})"
    if [[ $PKG == apt ]]; then
        export DEBIAN_FRONTEND=noninteractive
        wait_for_apt_lock
        apt-get update -qq
        # python3.12 is the system interpreter on Ubuntu 24.04; the package needs
        # >= 3.11. build-essential covers the rare source build; every dependency
        # here normally ships a manylinux wheel.
        apt-get install -y -qq python3 python3-venv python3-dev \
            build-essential nginx rsync curl
    else
        dnf install -y -q python3.12 python3.12-devel gcc gcc-c++ make nginx rsync
    fi
fi

if [[ $PKG == apt ]]; then PYTHON=python3; else PYTHON=python3.12; fi

echo "==> Python: $($PYTHON --version)"
$PYTHON - <<'PYCHECK'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"sbfsem needs Python >= 3.11, found {sys.version.split()[0]}")
PYCHECK

echo "==> Creating service account and directories"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir /srv/sbfsem --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$CACHE_DIR" "$SAVE_DIR" "$AUTH_DIR" "$CELL_RENDER_DIR" "$LIBRARY_DIR"

# Ownership, deliberately split:
#   app/    stays with whoever deploys, so rsync keeps working; the service only
#           reads it, and ProtectSystem=strict makes it read-only at runtime.
#   venv/   root-owned, service reads and executes.
#   cache/  the one path the service writes.
# Chowning the whole tree to the service user (the obvious thing) breaks the
# next rsync, and chowning it back breaks the cache.
DEPLOY_USER=$(stat -c '%U' "$APP_DIR")
chown root:root /srv/sbfsem

echo "==> Building the virtual environment"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    $PYTHON -m venv "$VENV_DIR"
fi
# setuptools and wheel in the venv itself let --quick build the package without
# an isolated build environment, which pip would otherwise fetch them into.
"$VENV_DIR/bin/pip" install --quiet --upgrade pip wheel "setuptools>=68"
# Install from inside the directory. `pip install "/abs/path[web]"` exits 0 and
# installs nothing -- pip parses the brackets as a requirement extra on a name
# it cannot resolve and gives up silently. `cd` + ".[web]" is the form that
# actually works.
# Build from the synced source, never from what an earlier build left behind.
# setuptools copies a file into build/lib only when the source is NEWER than
# the copy already there, and rsync keeps the sender's timestamps: after a
# deploy from a fresh checkout (files stamped at extraction) the next deploy
# from a working tree (files stamped when last edited, earlier) found every
# copy "up to date" and installed the old code -- the service restarted clean
# and served routes that were no longer in the tree (2026-09-21).
rm -rf "$APP_DIR/build"
( cd "$APP_DIR" && "$VENV_DIR/bin/pip" install --quiet ".[web]" )

# Optional: MeshLab remeshing (the [meshlab] extra: pymeshlab, GPL-3, ~300 MB
# installed; sbfsem never imports it, it runs it in a child process). Best
# effort on purpose -- a failed install only leaves the MeshLab control grayed
# out (/api/build says "meshlab": false); it must never fail a deploy.
# SBFSEM_MESHLAB=0 skips it.
if [[ ${SBFSEM_MESHLAB:-1} == 1 ]]; then
    if ! "$VENV_DIR/bin/python" -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('pymeshlab') is None)"; then
        echo "==> Installing pymeshlab (optional MeshLab remeshing)"
        if [[ $PKG == apt ]]; then
            # The wheel links libGL/libOpenGL even for geometry-only filters; no display is needed.
            wait_for_apt_lock && apt-get install -y -qq libgl1 libopengl0 libglu1-mesa libgomp1 \
                || echo "    could not install pymeshlab's system libraries"
        fi
        "$VENV_DIR/bin/pip" install --quiet "pymeshlab>=2023.12" \
            || echo "    pymeshlab did not install; the MeshLab option stays unavailable"
    fi
    # Proof it runs here, not just that it installed: one tetrahedron through the worker.
    if ( cd "$APP_DIR/src" && "$VENV_DIR/bin/python" - <<'PYMESHLAB'
import sys
from sbfsem.geometry import meshlab
from sbfsem.geometry.primitives import sphere
if not meshlab.available():
    sys.exit(1)
out = meshlab.remesh(sphere(1.0, divisions=16), "qem", fraction=0.5, timeout_s=120)
assert len(out.faces) > 0
PYMESHLAB
    ); then
        echo "    MeshLab remeshing available"
    else
        echo "    MeshLab remeshing NOT available on this host (the app grays the control out)"
    fi
fi

echo "==> Verifying the install"
"$VENV_DIR/bin/python" - <<'PYVERIFY'
import sbfsem
from sbfsem.core.registry import read_xy_offset, read_boundary_markers
from sbfsem_web.app import create_app
assert read_xy_offset("i") is not None, "packaged data missing"
assert read_boundary_markers("i", "GCL") is not None, "boundary markers missing"
create_app()
print(f"    sbfsem {sbfsem.__version__} imports, packaged data present")
PYVERIFY

chown -R "$SERVICE_USER:$SERVICE_USER" "$CACHE_DIR" "$SAVE_DIR" "$AUTH_DIR" "$CELL_RENDER_DIR" "$LIBRARY_DIR"
chmod 750 "$CACHE_DIR" "$SAVE_DIR" "$AUTH_DIR" "$CELL_RENDER_DIR" "$LIBRARY_DIR"
chown -R root:root "$VENV_DIR"
chmod -R a+rX "$VENV_DIR"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR"
chmod -R a+rX "$APP_DIR"

echo "==> Installing the systemd unit"
install -m 0644 "$APP_DIR/deploy/sbfsem-web.service" /etc/systemd/system/sbfsem-web.service
systemctl daemon-reload
systemctl enable sbfsem-web
systemctl restart sbfsem-web

echo "==> Installing the nginx site"
# The webroot the ACME challenge is served from, in both configs.
mkdir -p /var/www/certbot/.well-known/acme-challenge
chown -R www-data:www-data /var/www/certbot 2>/dev/null || true
chmod -R a+rX /var/www/certbot

# Pick the TLS config only when a certificate actually exists.  Installing it
# unconditionally would make `nginx -t` fail on a host that has never run
# certbot, taking the whole site down on a fresh deploy.
if [[ -s /etc/letsencrypt/live/$SITE_DOMAIN/fullchain.pem ]]; then
    echo "    certificate found for $SITE_DOMAIN -- installing the TLS config"
    install -m 0644 "$APP_DIR/deploy/nginx-sbfsem-tls.conf" "$NGINX_SITE"
else
    echo "    no certificate for $SITE_DOMAIN -- installing the HTTP config"
    echo "    (get one: sudo bash $APP_DIR/deploy/certbot-setup.sh $SITE_DOMAIN you@example.com)"
    install -m 0644 "$APP_DIR/deploy/nginx-sbfsem.conf" "$NGINX_SITE"
fi
if [[ $PKG == apt ]]; then
    ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/sbfsem
    rm -f /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl enable nginx
systemctl restart nginx

wait_for_service
# Only now: a run that failed part way leaves no stamp, so the next --quick does
# the full run again.
printf '%s\n' "$STAMP" > "$STAMP_FILE"
chmod 644 "$STAMP_FILE"
echo "    install stamp ${STAMP:0:12} written: the next --quick may skip the full run"
