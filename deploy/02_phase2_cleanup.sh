#!/bin/bash
# PHASE 2: Clean Up Hummingbot
# WARNING: This will DELETE Hummingbot and free up space

set -e

echo "========================================================================="
echo "PHASE 2: CLEAN UP HUMMINGBOT"
echo "========================================================================="
echo ""

# Create backup
echo "[1/7] Creating backup of Hummingbot configs..."
mkdir -p ~/backups/hummingbot_$(date +%Y%m%d)
if [ -d ~/hummingbot_files ]; then
    cp -r ~/hummingbot_files ~/backups/hummingbot_$(date +%Y%m%d)/ 2>/dev/null || true
    echo "✓ Backup created: ~/backups/hummingbot_$(date +%Y%m%d)/"
else
    echo "⚠️ No Hummingbot files to backup"
fi

# Stop Docker containers
echo ""
echo "[2/7] Stopping Hummingbot Docker containers..."
docker ps -a -q --filter ancestor=hummingbot/hummingbot 2>/dev/null | while read container; do
    echo "  Stopping container: $container"
    docker stop "$container" 2>/dev/null || true
done
echo "✓ Containers stopped"

# Remove Docker containers
echo ""
echo "[3/7] Removing Hummingbot Docker containers..."
docker ps -a -q --filter ancestor=hummingbot/hummingbot 2>/dev/null | while read container; do
    echo "  Removing container: $container"
    docker rm "$container" 2>/dev/null || true
done
echo "✓ Containers removed"

# Remove Docker images
echo ""
echo "[4/7] Removing Hummingbot Docker images..."
docker images | grep hummingbot | awk '{print $3}' | while read image; do
    echo "  Removing image: $image"
    docker rmi "$image" 2>/dev/null || true
done
echo "✓ Docker images removed"

# Stop processes
echo ""
echo "[5/7] Stopping Hummingbot processes..."
pkill -f hummingbot || true
sleep 2
ps aux | grep -f hummingbot | grep -v grep || echo "✓ No Hummingbot processes running"

# Remove directories
echo ""
echo "[6/7] Removing Hummingbot directories..."
rm -rf ~/hummingbot
echo "  ✓ Removed ~/hummingbot"
rm -rf ~/hummingbot_files
echo "  ✓ Removed ~/hummingbot_files"
rm -rf ~/.hummingbot
echo "  ✓ Removed ~/.hummingbot"
rm -rf ~/hummingbot-*
echo "  ✓ Removed ~/hummingbot-*"

# Remove Miniconda (created for Hummingbot)
echo ""
echo "[7/7] Removing Miniconda..."
rm -rf ~/miniconda3
echo "  ✓ Removed ~/miniconda3"
rm -rf ~/.conda
echo "  ✓ Removed ~/.conda"

# Reclaim disk space
echo ""
echo "========================================================================="
echo "RECLAIMING DISK SPACE"
echo "========================================================================="

echo "[1/3] Cleaning package cache..."
sudo apt-get clean
sudo apt-get autoclean
echo "✓ Package cache cleaned"

echo ""
echo "[2/3] Cleaning system logs..."
sudo journalctl --vacuum-time=1d 2>/dev/null || true
echo "✓ System logs cleaned"

echo ""
echo "[3/3] Checking disk space..."
echo "Before cleanup was around 5-10GB, checking current..."
df -h /

echo ""
echo "========================================================================="
echo "PHASE 2 COMPLETE - HUMMINGBOT REMOVED"
echo "========================================================================="
echo ""
