#!/bin/sh
# nginx-entrypoint.sh
# Starts nginx with HTTP-only config until the Let's Encrypt cert exists,
# then reloads with the full HTTPS config.
#
# Mounts expected:
#   /etc/nginx/conf.d/default.conf    — full HTTPS config (read-only)
#   /etc/nginx/conf.d/bootstrap.conf  — HTTP-only bootstrap (read-only)
#   /etc/letsencrypt                  — certbot volume

CERT="/etc/letsencrypt/live/planetflow.app/fullchain.pem"
CONF_DIR="/etc/nginx/conf.d"
ACTIVE="${CONF_DIR}/active.conf"

# Pick starting config
if [ ! -f "$CERT" ]; then
    echo "[nginx] No SSL cert found — starting in HTTP-only bootstrap mode."
    cp "${CONF_DIR}/bootstrap.conf" "$ACTIVE"
else
    echo "[nginx] SSL cert found — starting with full HTTPS config."
    cp "${CONF_DIR}/default.conf" "$ACTIVE"
fi

# Remove the named conf files so only active.conf is loaded
# (nginx loads all *.conf in conf.d)
mv "${CONF_DIR}/default.conf" "${CONF_DIR}/default.conf.bak" 2>/dev/null || true
mv "${CONF_DIR}/bootstrap.conf" "${CONF_DIR}/bootstrap.conf.bak" 2>/dev/null || true

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
    cp "${CONF_DIR}/default.conf.bak" "$ACTIVE"
    nginx -s reload
fi

wait $NGINX_PID
