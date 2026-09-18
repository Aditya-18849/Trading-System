#!/usr/bin/env bash
# ==============================================================================
# ALGO TRADE PRO — 1-Click Oracle Cloud Ubuntu Deployment Script
# ==============================================================================
set -e

echo "=============================================================================="
echo "    Starting ALGO TRADE PRO Backend Deployment on Oracle Cloud VM"
echo "=============================================================================="

# 1. Update system packages
echo "[1/6] Updating system packages..."
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw fail2ban ca-certificates gnupg lsb-release

# 2. Install Docker & Docker Compose
echo "[2/6] Installing Docker & Docker Compose..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    rm get-docker.sh
fi

sudo apt install -y docker-compose-plugin

# 3. Configure Ubuntu IPTables / Firewall (Oracle Cloud requires iptables rules)
echo "[3/6] Opening firewall ports (22, 80, 443, 8000)..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT || true
sudo netfilter-persistent save 2>/dev/null || true

# 4. Prepare Environment Configuration
echo "[4/6] Setting up environment configuration..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example. Please update your broker API keys in .env"
fi

# Ensure database and logs directories exist
mkdir -p logs backups

# 5. Build and launch Backend & PostgreSQL containers
echo "[5/6] Launching backend services via Docker..."
docker compose up -d --build postgres redis app

# 6. Verify health
echo "[6/6] Verifying API health..."
sleep 5
docker compose ps

echo "=============================================================================="
echo " [SUCCESS] Backend is running on Oracle Cloud!"
echo " Public API endpoint: http://YOUR_ORACLE_PUBLIC_IP:8000"
echo " Interactive Docs:   http://YOUR_ORACLE_PUBLIC_IP:8000/docs"
echo " Live WebSocket:     ws://YOUR_ORACLE_PUBLIC_IP:8000/ws/live"
echo "=============================================================================="
