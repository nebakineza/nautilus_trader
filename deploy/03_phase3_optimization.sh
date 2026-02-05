#!/bin/bash
# PHASE 3: System Optimization for Low-Latency Trading
# Configures kernel parameters, CPU governors, and file limits

set -e

echo "========================================================================="
echo "PHASE 3: SYSTEM OPTIMIZATION FOR LOW-LATENCY TRADING"
echo "========================================================================="
echo ""

# Update system
echo "[1/7] Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y
echo "✓ System packages updated"

# Install performance tools
echo ""
echo "[2/7] Installing performance tools..."
sudo apt-get install -y htop iotop sysstat net-tools cpufrequtils
echo "✓ Performance tools installed"

# Install build tools
echo ""
echo "[3/7] Installing build tools..."
sudo apt-get install -y build-essential git curl wget python3.12 python3.12-venv python3.12-dev
echo "✓ Build tools installed"

# Create sysctl configuration for trading
echo ""
echo "[4/7] Configuring kernel parameters for low-latency..."
sudo tee /etc/sysctl.d/99-trading-performance.conf > /dev/null << 'SYSCTL'
# Network performance tuning
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
net.core.rmem_default = 16777216
net.core.wmem_default = 16777216
net.ipv4.tcp_rmem = 4096 87380 134217728
net.ipv4.tcp_wmem = 4096 65536 134217728
net.ipv4.tcp_congestion_control = bbr
net.core.netdev_max_backlog = 5000
net.ipv4.tcp_max_syn_backlog = 8096
net.ipv4.tcp_slow_start_after_idle = 0

# Reduce swappiness for performance
vm.swappiness = 10

# File descriptor limits
fs.file-max = 2097152

# Disable TCP timestamps for lower overhead
net.ipv4.tcp_timestamps = 0

# Enable TCP Fast Open
net.ipv4.tcp_fastopen = 3
SYSCTL
sudo sysctl -p /etc/sysctl.d/99-trading-performance.conf > /dev/null
echo "✓ Kernel parameters optimized"

# Set CPU governor to performance
echo ""
echo "[5/7] Setting CPU governor to performance mode..."
echo "performance" | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor > /dev/null
echo 'GOVERNOR="performance"' | sudo tee /etc/default/cpufrequtils > /dev/null
sudo systemctl restart cpufrequtils
echo "✓ CPU governor set to performance"

# Increase file descriptor limits
echo ""
echo "[6/7] Increasing file descriptor limits..."
sudo tee -a /etc/security/limits.conf > /dev/null << 'LIMITS'
ubuntu soft nofile 65536
ubuntu hard nofile 65536
ubuntu soft nproc 65536
ubuntu hard nproc 65536
LIMITS
echo "✓ File descriptor limits increased"

# Verify optimization
echo ""
echo "[7/7] Verifying system optimization..."
echo "CPU Governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
echo "Swappiness: $(cat /proc/sys/vm/swappiness)"
echo "File max: $(cat /proc/sys/fs/file-max)"
echo "TCP congestion control: $(cat /proc/sys/net/ipv4/tcp_congestion_control)"

echo ""
echo "========================================================================="
echo "PHASE 3 COMPLETE - SYSTEM OPTIMIZED"
echo "========================================================================="
echo ""
