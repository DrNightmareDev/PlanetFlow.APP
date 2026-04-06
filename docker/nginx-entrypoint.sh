#!/bin/sh
# nginx-entrypoint.sh
# Writes the correct nginx config before nginx starts.
# Sources are mounted as .conf.template (not .conf) so nginx never auto-loads them.
#
# Mounts expected:
#   /etc/nginx/templates/https.conf.template  — full HTTPS config (NGINX_MODE=https)
#   /etc/nginx/templates/http.conf.template   — HTTP-only bootstrap
#   /etc/nginx/templates/proxy.conf.template  — HTTP-only proxy mode (NGINX_MODE=proxy)
#   /etc/letsencrypt                           — certbot volume
#
# NGINX_MODE values:
#   https  (default) — Let's Encrypt TLS, port 80+443
#   proxy            — upstream terminates TLS (OPNsense/Traefik/etc.), port 80 only
#   local            — local development, HTTP only (same behavior as proxy)

NGINX_MODE="${NGINX_MODE:-https}"
if [ "$NGINX_MODE" = "local" ]; then
    NGINX_MODE="proxy"
fi

CERT="/etc/letsencrypt/live/planetflow.app/fullchain.pem"
ACTIVE="/etc/nginx/conf.d/active.conf"
HTTPS_TPL="/etc/nginx/templates/https.conf.template"
HTTP_TPL="/etc/nginx/templates/http.conf.template"
PROXY_TPL="/etc/nginx/templates/proxy.conf.template"

mkdir -p /etc/nginx/conf.d

if [ "$NGINX_MODE" = "proxy" ]; then
    echo "[nginx] NGINX_MODE=proxy — HTTP-only reverse-proxy mode (upstream handles TLS)."
    cp "$PROXY_TPL" "$ACTIVE"
    exec nginx -g "daemon off;"
fi

# Default: https mode with Let's Encrypt
if [ ! -f "$CERT" ]; then
    echo "[nginx] No SSL cert found — starting in HTTP-only bootstrap mode."
    cp "$HTTP_TPL" "$ACTIVE"
else
    echo "[nginx] SSL cert found — starting with full HTTPS config."
    cp "$HTTPS_TPL" "$ACTIVE"
fi

# Start nginx in background
nginx -g "daemon off;" &
NGINX_PID=$!

# If we started in bootstrap mode, poll for cert and reload
if [ ! -f "$CERT" ]; then
    echo "[nginx] Waiting for SSL cert at $CERT ..."
    while [ ! -f "$CERT" ]; do
        sleep 5
    done
    echo "[nginx] SSL cert detected — reloading with HTTPS config."
    cp "$HTTPS_TPL" "$ACTIVE"
    nginx -s reload
fi

wait $NGINX_PID
