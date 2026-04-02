#!/usr/bin/env bash
# ============================================================
# PlanetFlow — Hetzner Server Setup Script
# Ubuntu 22.04 / 24.04 LTS
#
# Run as root on a fresh Hetzner VM:
#   bash setup_hetzner.sh
# ============================================================
set -euo pipefail

DOMAIN="planetflow.app"
APP_DIR="/opt/planetflow"
GIT_REPO="git@github.com:DrNightmareDev/PlanetFlow.APP.git"
COMPOSE_VERSION="2.27.0"

# ── 1. System update ─────────────────────────────────────────
echo "==> Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    git \
    curl \
    ca-certificates \
    gnupg \
    ufw \
    fail2ban

# ── 2. Docker Engine ─────────────────────────────────────────
echo "==> Installing Docker..."
if ! command -v docker &>/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
    echo "Docker installed: $(docker --version)"
else
    echo "Docker already installed: $(docker --version)"
fi

# ── 3. Firewall ──────────────────────────────────────────────
echo "==> Configuring UFW firewall..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
echo "UFW status:"
ufw status verbose

# ── 4. Clone repository ──────────────────────────────────────
echo "==> Cloning PlanetFlow repository..."
if [ -d "$APP_DIR" ]; then
    echo "Directory $APP_DIR already exists — pulling latest..."
    git -C "$APP_DIR" pull
else
    git clone "$GIT_REPO" "$APP_DIR"
fi
cd "$APP_DIR"

# ── 5. Create .env from example ──────────────────────────────
echo "==> Setting up .env..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ""
    echo "  !! .env created from .env.example"
    echo "  !! Edit $APP_DIR/.env before starting the stack."
    echo "  !! Required: DB_PASSWORD, SECRET_KEY, EVE_CLIENT_ID, EVE_CLIENT_SECRET, EVE_CALLBACK_URL"
fi

# ── 6. Initial TLS certificate (first boot only) ─────────────
echo ""
echo "==> First-time TLS certificate setup"
echo "    This requires DNS A record for $DOMAIN to point to this server's IP."
echo ""
read -rp "    Get certificate now? (y/N): " GET_CERT
if [[ "${GET_CERT,,}" == "y" ]]; then
    # Start nginx with HTTP-only to serve ACME challenge
    # Temporarily use a plain HTTP-only nginx config for initial cert issuance
    docker run --rm \
        -v "$APP_DIR/certbot_www:/var/www/certbot" \
        -v "$APP_DIR/certbot_conf:/etc/letsencrypt" \
        -p 80:80 \
        nginx:alpine \
        sh -c "mkdir -p /var/www/certbot && nginx -g 'daemon off;' &
               sleep 3 && echo 'nginx ready'" &
    NGINX_PID=$!

    docker run --rm \
        -v "$APP_DIR/certbot_www:/var/www/certbot" \
        -v "$APP_DIR/certbot_conf:/etc/letsencrypt" \
        certbot/certbot certonly \
        --webroot \
        --webroot-path /var/www/certbot \
        --email "admin@$DOMAIN" \
        --agree-tos \
        --no-eff-email \
        -d "$DOMAIN" \
        -d "www.$DOMAIN"

    kill $NGINX_PID 2>/dev/null || true
    echo "Certificate issued successfully."
else
    echo "    Skipped. Run certbot manually or let the certbot container handle it on first startup."
    echo "    NOTE: nginx will fail to start until certificates exist."
    echo "    Quick manual issue: see docs/tls_setup.md"
fi

# ── 7. Done ──────────────────────────────────────────────────
echo ""
echo "============================================================"
echo " Setup complete."
echo ""
echo " Next steps:"
echo "   1. Edit /opt/planetflow/.env (see section below)"
echo "   2. cd /opt/planetflow"
echo "   3. docker compose up -d"
echo "   4. docker compose logs -f app"
echo "============================================================"
