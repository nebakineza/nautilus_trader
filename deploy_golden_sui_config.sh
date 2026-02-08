#!/bin/bash
# Deploy SUIUSDT Golden Configuration to VPS
# Generated: 2026-02-05

set -e

echo "========================================"
echo "DEPLOYING SUIUSDT GOLDEN CONFIG TO VPS"
echo "========================================"

VPS="sentinel-vps"
REMOTE_FILE="/home/ubuntu/trading/strategy_pkg/run_vip_production_v3.py"
BACKUP_FILE="/home/ubuntu/trading/strategy_pkg/run_vip_production_v3.py.backup_$(date +%Y%m%d_%H%M%S)"

echo ""
echo "1. Creating backup..."
ssh $VPS "cp $REMOTE_FILE $BACKUP_FILE"
echo "   ✅ Backup created: $BACKUP_FILE"

echo ""
echo "2. Updating SUIUSDT config with golden parameters..."

# Create Python update script
cat > /tmp/update_golden_config.py << 'EOFPYTHON'
import sys

config_file = sys.argv[1]

with open(config_file, 'r') as f:
    lines = f.readlines()

# Find SUIUSDT block and update parameters
in_sui_block = False
updated_lines = []
i = 0

while i < len(lines):
    line = lines[i]
    
    # Detect start of SUIUSDT block
    if '"symbol": "SUIUSDT"' in line:
        in_sui_block = True
        updated_lines.append(line)
        i += 1
        continue
    
    # Update parameters within SUI block
    if in_sui_block:
        if '"max_position_qty"' in line:
            updated_lines.append('        "max_position_qty": Decimal("800.0"),  # Golden: increased\n')
        elif '"guard_threshold_bps"' in line:
            updated_lines.append('        "guard_threshold_bps": Decimal("25.0"),  # Golden: 25bps\n')
        elif '"refresh_interval"' in line:
            updated_lines.append('        "refresh_interval": 5000,  # Golden: 5s\n')
        elif '"refresh_offset"' in line:
            updated_lines.append('        "refresh_offset": 99,  # Golden: unique offset\n')
        elif '"liquidity_high_qty"' in line:
            updated_lines.append('        "liquidity_high_qty": Decimal("5000.0"),  # Golden\n')
        elif '"liquidity_low_qty"' in line:
            updated_lines.append('        "liquidity_low_qty": Decimal("200.0"),  # Golden\n')
        elif '"ofi_enabled"' in line:
            updated_lines.append('        "ofi_enabled": False,  # Golden: DISABLED\n')
        elif '"ofi_max_bps"' in line:
            updated_lines.append('        "ofi_max_bps": Decimal("0.0"),  # Golden: disabled\n')
        elif '"min_quote_lifetime_ms"' in line:
            updated_lines.append('        "min_quote_lifetime_ms": 2000,  # Golden: 2s\n')
        elif '"internal_price_delta_limit"' in line:
            updated_lines.append('        "internal_price_delta_limit": Decimal("8.0"),  # Golden\n')
        else:
            updated_lines.append(line)
        
        # End of SUI block
        if line.strip() == '},':
            in_sui_block = False
    else:
        updated_lines.append(line)
    
    i += 1

with open(config_file, 'w') as f:
    f.writelines(updated_lines)

print("✅ Golden config applied")
EOFPYTHON

# Copy and run on VPS
scp /tmp/update_golden_config.py $VPS:/tmp/
ssh $VPS "python3 /tmp/update_golden_config.py $REMOTE_FILE"

echo ""
echo "3. Verifying update..."
ssh $VPS "grep -A 5 'guard_threshold_bps' $REMOTE_FILE | grep SUIUSDT -A 5 | head -10"

echo ""
echo "4. Restarting service..."
ssh $VPS "sudo systemctl restart vip_sui_heavy.service"

echo ""
echo "5. Checking service status..."
sleep 3
ssh $VPS "sudo systemctl status vip_sui_heavy.service | head -15"

echo ""
echo "========================================"
echo "✅ GOLDEN CONFIG DEPLOYED!"
echo "========================================"
echo ""
echo "Expected Performance:"
echo "  - Daily Volume: \$26,224"
echo "  - Daily PnL:    \$51.37"
echo "  - Daily Fills:  ~224"
echo ""
echo "Monitor logs:"
echo "  ssh $VPS 'tail -f /home/ubuntu/trading/logs/*.log'"
echo ""
