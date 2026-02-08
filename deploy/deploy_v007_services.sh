#!/bin/bash
# Deploy v007 FORTRESS services to VPS
# Creates systemd service files for all 8 pairs

set -e

# SSH to VPS and create service files
ssh sentinel-vps 'cat > /tmp/vip_sui_v7.service << EOF
[Unit]
Description=VIP MM v007 FORTRESS - SUI (MAINACC_01)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading/strategy_pkg
EnvironmentFile=/home/ubuntu/trading/.env
ExecStart=/home/ubuntu/trading/runtime_env/bin/python run_vip_sui_v7.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/vip_sui_v7.service /etc/systemd/system/'

ssh sentinel-vps 'cat > /tmp/vip_link_v7.service << EOF
[Unit]
Description=VIP MM v007 FORTRESS - LINK (MAINACC_02)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading/strategy_pkg
EnvironmentFile=/home/ubuntu/trading/.env
ExecStart=/home/ubuntu/trading/runtime_env/bin/python run_vip_link_v7.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/vip_link_v7.service /etc/systemd/system/'

ssh sentinel-vps 'cat > /tmp/vip_avax_v7.service << EOF
[Unit]
Description=VIP MM v007 FORTRESS - AVAX (MAINACC_03)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading/strategy_pkg
EnvironmentFile=/home/ubuntu/trading/.env
ExecStart=/home/ubuntu/trading/runtime_env/bin/python run_vip_avax_v7.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/vip_avax_v7.service /etc/systemd/system/'

ssh sentinel-vps 'cat > /tmp/vip_ena_v7.service << EOF
[Unit]
Description=VIP MM v007 FORTRESS - ENA (MAINACC_04)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading/strategy_pkg
EnvironmentFile=/home/ubuntu/trading/.env
ExecStart=/home/ubuntu/trading/runtime_env/bin/python run_vip_ena_v7.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/vip_ena_v7.service /etc/systemd/system/'

ssh sentinel-vps 'cat > /tmp/vip_near_v7.service << EOF
[Unit]
Description=VIP MM v007 FORTRESS - NEAR (MAINACC_05)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading/strategy_pkg
EnvironmentFile=/home/ubuntu/trading/.env
ExecStart=/home/ubuntu/trading/runtime_env/bin/python run_vip_near_v7.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/vip_near_v7.service /etc/systemd/system/'

ssh sentinel-vps 'cat > /tmp/vip_arb_v7.service << EOF
[Unit]
Description=VIP MM v007 FORTRESS - ARB (MAINACC_06)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading/strategy_pkg
EnvironmentFile=/home/ubuntu/trading/.env
ExecStart=/home/ubuntu/trading/runtime_env/bin/python run_vip_arb_v7.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/vip_arb_v7.service /etc/systemd/system/'

ssh sentinel-vps 'cat > /tmp/vip_ondo_v7.service << EOF
[Unit]
Description=VIP MM v007 FORTRESS - ONDO (MAINACC_07)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading/strategy_pkg
EnvironmentFile=/home/ubuntu/trading/.env
ExecStart=/home/ubuntu/trading/runtime_env/bin/python run_vip_ondo_v7.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/vip_ondo_v7.service /etc/systemd/system/'

ssh sentinel-vps 'cat > /tmp/vip_ton_v7.service << EOF
[Unit]
Description=VIP MM v007 FORTRESS - TON (MAINACC_08)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/trading/strategy_pkg
EnvironmentFile=/home/ubuntu/trading/.env
ExecStart=/home/ubuntu/trading/runtime_env/bin/python run_vip_ton_v7.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/vip_ton_v7.service /etc/systemd/system/'

# Reload and start services
ssh sentinel-vps 'sudo systemctl daemon-reload'

echo "✅ All v007 service files created"
