#!/bin/bash
# Lightsail launch script -- paste into "Add launch script" when creating the
# instance. Runs once, as root, at first boot, before you can SSH in.
#
# Deliberately limited to things that cannot lock you out: package installs and
# security updates. Nothing here touches sshd, the firewall or user accounts. A
# launch script runs exactly once and cannot be edited afterwards, so a mistake
# in one means deleting the instance and starting over.
#
# Everything else is deploy/bootstrap.sh, which you can re-run as often as needed.

exec > >(tee -a /var/log/sbfsem-launch.log) 2>&1
echo "=== sbfsem launch script starting $(date -u) ==="

export DEBIAN_FRONTEND=noninteractive

# cloud-init races the apt daily timers on first boot; without this the first
# apt-get usually dies on a held dpkg lock.
for i in $(seq 1 60); do
    if ! fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; then
        break
    fi
    echo "waiting for apt lock ($i/60)"
    sleep 5
done

apt-get update -y

# What deploy/bootstrap.sh needs, installed now so the first run is quick.
apt-get install -y \
    python3 python3-venv python3-dev \
    build-essential \
    nginx \
    rsync \
    unattended-upgrades

# Security patches applied automatically. Worth having from the first boot
# rather than remembering later.
dpkg-reconfigure -f noninteractive unattended-upgrades

# The application tree, owned by the login user so the first rsync just works.
install -d -o ubuntu -g ubuntu /srv/sbfsem /srv/sbfsem/app /srv/sbfsem/cache

echo "=== sbfsem launch script finished $(date -u) ==="
echo "Next: rsync the code to /srv/sbfsem/app, then"
echo "      sudo bash /srv/sbfsem/app/deploy/bootstrap.sh"
