#!/bin/bash
# Deploy ETH, LINK, AVAX production services to VPS

set -e

VPS_USER="ubuntu"
VPS_HOST="13.228.94.51"
SSH_KEY="/home/seb/nebakineza/nautilus_trader/id_ed25519"
REMOTE_DIR="/home/ubuntu/trading"

echo "==> Deploying VIP ETH/LINK/AVAX production services to $VPS_HOST"

# Copy runner scripts
echo "==> Copying runner scripts..."
scp -i "$SSH_KEY" \
    scripts/runners/run_vip_eth_production.py \
    scripts/runners/run_vip_link_production.py \
    scripts/runners/run_vip_avax_production.py \
    "$VPS_USER@$VPS_HOST:$REMOTE_DIR/strategy_pkg/"

# Copy systemd service files
echo "==> Copying systemd service files..."
scp -i "$SSH_KEY" \
    deploy/vip_eth_production.service \
    deploy/vip_link_production.service \
    deploy/vip_avax_production.service \
    "$VPS_USER@$VPS_HOST:/tmp/"

# Install and start services
echo "==> Installing and starting services..."
ssh -i "$SSH_KEY" "$VPS_USER@$VPS_HOST" << 'ENDSSH'
    # Install service files
    sudo mv /tmp/vip_eth_production.service /etc/systemd/system/
    sudo mv /tmp/vip_link_production.service /etc/systemd/system/
    sudo mv /tmp/vip_avax_production.service /etc/systemd/system/

    # Reload systemd
    sudo systemctl daemon-reload

    # Enable services (auto-start on boot)
    sudo systemctl enable vip_eth_production.service
    sudo systemctl enable vip_link_production.service
    sudo systemctl enable vip_avax_production.service

    echo "Services installed. Start them with:"
    echo "  sudo systemctl start vip_eth_production"
    echo "  sudo systemctl start vip_link_production"
    echo "  sudo systemctl start vip_avax_production"
    echo ""
    echo "Check status with:"
    echo "  sudo systemctl status vip_eth_production"
    echo "  sudo systemctl status vip_link_production"
    echo "  sudo systemctl status vip_avax_production"
    echo ""
    echo "View logs with:"
    echo "  sudo journalctl -u vip_eth_production -f"
    echo "  sudo journalctl -u vip_link_production -f"
    echo "  sudo journalctl -u vip_avax_production -f"
ENDSSH

echo ""
echo "==> Deployment complete!"
echo ""
echo "Next steps:"
echo "1. SSH to VPS: ssh -i $SSH_KEY $VPS_USER@$VPS_HOST"
echo "2. Start services:"
echo "   sudo systemctl start vip_eth_production"
echo "   sudo systemctl start vip_link_production"
echo "   sudo systemctl start vip_avax_production"
echo "3. Monitor:"
echo "   sudo journalctl -u vip_eth_production -f"
echo "   tail -f /home/ubuntu/trading/logs/VIP1-PRODUCTION-ETH.service.log"
