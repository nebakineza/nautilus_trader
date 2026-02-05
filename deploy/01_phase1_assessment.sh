#!/bin/bash
# PHASE 1: Initial Assessment & Backup
# Run this first to assess current state

set -e

echo "========================================================================="
echo "PHASE 1: INITIAL ASSESSMENT & BACKUP"
echo "========================================================================="
echo ""

# Test sudo access
echo "[1/6] Testing sudo access..."
sudo -v > /dev/null && echo "✓ Sudo access confirmed" || exit 1

# Check current disk usage
echo ""
echo "[2/6] Current disk usage:"
df -h | head -5
echo ""
echo "Largest directories:"
du -sh ~/* 2>/dev/null | sort -hr | head -10

# Check system info
echo ""
echo "[3/6] System information:"
echo "OS: $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
echo "Kernel: $(uname -r)"
echo "CPU cores: $(nproc)"
echo "RAM: $(free -h | grep Mem | awk '{print $2}')"

# Check Hummingbot installation
echo ""
echo "[4/6] Checking Hummingbot installation..."
if [ -d ~/hummingbot ] || [ -d ~/hummingbot_files ]; then
    echo "✓ Hummingbot directories found:"
    ls -lad ~/hummingbot* 2>/dev/null || true
else
    echo "⚠️ No Hummingbot directories found"
fi

# Check Docker
echo ""
echo "[5/6] Checking Docker..."
if command -v docker &> /dev/null; then
    echo "✓ Docker installed: $(docker --version)"
    docker ps -a | grep -i hummingbot && echo "  Found Hummingbot containers" || echo "  No Hummingbot containers"
else
    echo "⚠️ Docker not installed"
fi

# Check running processes
echo ""
echo "[6/6] Checking for running trading processes..."
ps aux | grep -E "hummingbot|trading|nautilus" | grep -v grep && echo "Found running processes" || echo "✓ No trading processes running"

echo ""
echo "========================================================================="
echo "ASSESSMENT COMPLETE"
echo "========================================================================="
echo ""
echo "Ready to proceed? (Review above output)"
echo ""
