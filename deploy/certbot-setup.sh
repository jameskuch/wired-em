#!/usr/bin/env bash
# Obtain a Let's Encrypt certificate for the site, once.
#
# Run ON THE SERVER:
#
#   sudo bash /srv/sbfsem/app/deploy/certbot-setup.sh sbfsem-tools.com you@example.com
#
# Uses the webroot challenge rather than certbot's nginx plugin.  The plugin
# rewrites /etc/nginx/sites-available/sbfsem in place, and bootstrap.sh
# reinstalls that file from the repository on every deploy -- so the plugin's
# work would vanish at the next redeploy and TLS would silently revert to HTTP.
# The webroot method touches nothing nginx owns, so both survive.
#
# Only port 80 needs to be reachable for this.  Open 443 afterwards.

set -euo pipefail

DOMAIN=${1:?"usage: $0 <domain> <email>"}
EMAIL=${2:?"usage: $0 <domain> <email>"}
WEBROOT=/var/www/certbot

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash $0 $DOMAIN $EMAIL" >&2
    exit 1
fi

echo "==> Checking $DOMAIN resolves here"
RESOLVED=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)
MINE=$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null || echo "")
if [[ -n $RESOLVED && -n $MINE && $RESOLVED != "$MINE" ]]; then
    echo "    $DOMAIN -> $RESOLVED but this host is $MINE" >&2
    echo "    Let's Encrypt validates over DNS from outside; fix the A record first." >&2
    exit 1
fi
echo "    $DOMAIN -> ${RESOLVED:-unknown} (this host: ${MINE:-unknown})"

echo "==> Installing certbot"
if ! command -v certbot >/dev/null 2>&1; then
    for _ in $(seq 1 60); do
        fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
        sleep 5
    done
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot
fi
certbot --version

echo "==> Preparing the challenge webroot"
mkdir -p "$WEBROOT/.well-known/acme-challenge"
chown -R www-data:www-data "$WEBROOT"
chmod -R a+rX "$WEBROOT"

# Prove the webroot is actually reachable before asking Let's Encrypt to try;
# a failed real attempt counts against the rate limit, a failed local one does not.
TOKEN="preflight-$$"
echo ok > "$WEBROOT/.well-known/acme-challenge/$TOKEN"
if ! curl -fsS --max-time 10 "http://$DOMAIN/.well-known/acme-challenge/$TOKEN" | grep -q ok; then
    rm -f "$WEBROOT/.well-known/acme-challenge/$TOKEN"
    echo "    Challenge path is not reachable over HTTP." >&2
    echo "    Deploy the current nginx config first (it serves $WEBROOT), then retry." >&2
    exit 1
fi
rm -f "$WEBROOT/.well-known/acme-challenge/$TOKEN"
echo "    challenge path reachable"

echo "==> Requesting the certificate"
certbot certonly \
    --webroot -w "$WEBROOT" \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --non-interactive \
    --keep-until-expiring

echo "==> Renewal"
# The certbot package ships a systemd timer; make sure it is running, and
# reload nginx after a renewal so the new certificate is actually served.
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'HOOK'
#!/bin/sh
# Renewal writes new files; nginx keeps serving the old ones until reloaded.
systemctl reload nginx
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
systemctl enable --now certbot.timer 2>/dev/null || true
systemctl list-timers certbot.timer --no-pager 2>/dev/null | head -3 || true

echo
echo "Certificate:"
certbot certificates 2>/dev/null | sed -n '/Certificate Name/,/Expiry/p' | head -8
echo
echo "Next: re-run bootstrap.sh to install the TLS nginx config, then open"
echo "port 443 in the Lightsail firewall."
