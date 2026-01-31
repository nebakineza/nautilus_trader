# AWS EC2 Deployment Instructions - Nautilus Trader

## Quick Start (5 minutes)

### 1. SSH to your AWS Instance

```bash
ssh ubuntu@13.228.94.51
```

### 2. Copy deployment scripts to instance

From your local machine, copy the entire deploy folder:

```bash
scp -r /home/seb/nebakineza/nautilus_trader/deploy/ ubuntu@13.228.94.51:~/
```

### 3. Run the master deployment script

```bash
ssh ubuntu@13.228.94.51 "bash ~/deploy/run_all.sh"
```

This will automatically run all 7 phases with prompts between each phase.

---

## Detailed Phase Breakdown

### Phase 1: Initial Assessment & Backup (5 min)
**Purpose**: Check current system state and create backups

**What it does**:
- Checks disk space and memory
- Verifies Hummingbot installation
- Lists running processes
- Creates backup directory

**Run individually**:
```bash
bash ~/deploy/01_phase1_assessment.sh
```

### Phase 2: Clean Up Hummingbot (10 min)
**Purpose**: Remove Hummingbot and reclaim disk space

**What it does**:
- Backs up Hummingbot configs
- Stops Docker containers
- Removes Hummingbot directories
- Reclaims 2-5GB of disk space
- Cleans system logs

**Run individually**:
```bash
bash ~/deploy/02_phase2_cleanup.sh
```

### Phase 3: System Optimization (15 min)
**Purpose**: Tune EC2 instance for low-latency trading

**What it does**:
- Updates system packages
- Installs performance tools
- Configures kernel parameters for networking
- Sets CPU governor to performance mode
- Increases file descriptor limits

**Run individually**:
```bash
bash ~/deploy/03_phase3_optimization.sh
```

### Phase 4: Install NautilusTrader (20 min)
**Purpose**: Install NautilusTrader and dependencies

**What it does**:
- Installs Python 3.12
- Installs Rust compiler
- Clones NautilusTrader repository
- Creates Python virtual environment
- Installs NautilusTrader from source

**Run individually**:
```bash
bash ~/deploy/04_phase4_install_nautilus.sh
```

### Phase 5: Deploy Strategy (10 min)
**Purpose**: Copy strategy files and create configuration

**What it does**:
- Creates strategy directory
- Deploys specialized low-capital strategy
- Creates live trading configuration
- Creates environment file (.env)
- Creates runner script

**Run individually**:
```bash
bash ~/deploy/05_phase5_deploy_strategy.sh
```

### Phase 6: Safety Checks (5 min)
**Purpose**: Verify all kill switches and safety mechanisms

**What it does**:
- Verifies kill switch configuration (-$100)
- Creates emergency stop script
- Creates monitoring script
- Creates safety verification file

**Run individually**:
```bash
bash ~/deploy/06_phase6_safety_checks.sh
```

### Phase 7: Pre-Flight Checklist (5 min)
**Purpose**: Final verification before trading

**What it does**:
- Verifies file structure
- Checks environment configuration
- Verifies Python environment
- Checks disk and memory
- Displays final checklist

**Run individually**:
```bash
bash ~/deploy/07_phase7_preflight.sh
```

### Phase 8: Launch Trading
**Purpose**: Start live trading

**What it does**:
- Checks for existing trading processes
- Verifies trading mode (testnet vs mainnet)
- Starts the trading process
- Logs all activity

**Run individually**:
```bash
bash ~/deploy/08_phase8_launch.sh
```

---

## Critical: Add API Keys

After Phase 5, you MUST edit the .env file with your API credentials:

```bash
nano ~/trading/nautilus_trader/.env
```

The file will look like:
```
BYBIT_API_KEY=your_key_here
BYBIT_API_SECRET=your_secret_here
BYBIT_TESTNET=true
```

Update with your actual BYBIT API keys. The template is located at:
```bash
cat ~/deploy/.env.template
```

---

## Starting Live Trading

### Option 1: Start with Master Script (Recommended)

```bash
bash ~/deploy/run_all.sh
```

This runs all phases with prompts between each.

### Option 2: Run Individual Phases

```bash
# Phase by phase
bash ~/deploy/01_phase1_assessment.sh
bash ~/deploy/02_phase2_cleanup.sh
bash ~/deploy/03_phase3_optimization.sh
bash ~/deploy/04_phase4_install_nautilus.sh
bash ~/deploy/05_phase5_deploy_strategy.sh
bash ~/deploy/06_phase6_safety_checks.sh
bash ~/deploy/07_phase7_preflight.sh
bash ~/deploy/08_phase8_launch.sh
```

### Option 3: Quick Start (After Full Deployment)

```bash
cd ~/trading/nautilus_trader
source .venv/bin/activate
python run_live_lowcap.py
```

---

## Monitoring & Management

### Check Trading Status

```bash
# Monitor system resources and trading activity
bash ~/trading/nautilus_trader/monitor.sh

# Or use htop for real-time monitoring
htop
```

### View Live Logs

```bash
# Follow live logs
tail -f ~/trading/nautilus_trader/logs/*.log

# View recent logs
less ~/trading/nautilus_trader/logs/trading_*.log
```

### Emergency Stop

If you need to IMMEDIATELY stop trading:

```bash
# Graceful stop (use this first)
# Ctrl+C in the terminal where trading is running

# Emergency forceful stop
bash ~/trading/nautilus_trader/emergency_stop.sh

# Or directly kill the process
pkill -9 -f "run_live_lowcap.py"
```

---

## Important Configuration Files

After deployment, these files will be ready:

| File | Location | Purpose |
|------|----------|---------|
| Strategy | `~/trading/nautilus_trader/strategy/hft_obi_bybit_spot_mm_lowcap_v001.py` | Trading algorithm |
| Config | `~/trading/nautilus_trader/config/live_lowcap_strategy.py` | Strategy parameters |
| Environment | `~/trading/nautilus_trader/.env` | API credentials |
| Runner | `~/trading/nautilus_trader/run_live_lowcap.py` | Start script |
| Emergency | `~/trading/nautilus_trader/emergency_stop.sh` | Kill switch |
| Monitor | `~/trading/nautilus_trader/monitor.sh` | Status checker |

---

## Deployment Timeline

| Phase | Duration | Tasks |
|-------|----------|-------|
| 1. Assessment | 5 min | Check system state |
| 2. Cleanup | 10 min | Remove Hummingbot |
| 3. Optimization | 15 min | Tune for low-latency |
| 4. Install | 20 min | Install NautilusTrader |
| 5. Deploy | 10 min | Copy strategy files |
| 6. Safety | 5 min | Verify kill switches |
| 7. Pre-flight | 5 min | Final checks |
| 8. Launch | - | Start trading |
| **TOTAL** | **~70 min** | Full deployment |

---

## Safety Mechanisms

### Kill Switch (Automatic)
- **Trigger**: Loss exceeds -$100 USD (-20% of capital)
- **Action**: Strategy automatically liquidates all positions
- **Location**: `config/live_lowcap_strategy.py` line with `emergency_liquidation_loss_usd=-100.0`

### Emergency Stop (Manual)
- **Command**: `bash emergency_stop.sh`
- **Action**: Forcefully terminates all trading processes
- **Use When**: Ctrl+C doesn't work or immediate shutdown needed

### Position Limits
- **Max Position Size**: 0.0005 BTC (~$48.75)
- **Max Hold Time**: 120 seconds
- **Spread Range**: 1-5 basis points

### System Safeguards
- **Memory**: Min 512MB free
- **Disk**: Min 100MB free
- **Network**: TCP connection to BYBIT required
- **Python**: Python 3.12 required

---

## Troubleshooting

### "SSH: Connection refused"
- Check instance is running in AWS console
- Verify security group allows SSH (port 22)
- Verify you're using correct key pair
- Check instance has public IP: `13.228.94.51`

### "Hummingbot directories not found"
- Hummingbot may have already been removed
- Safe to continue, Phase 2 will skip

### "Docker: command not found"
- Docker was not installed on this instance
- Safe to continue, NautilusTrader doesn't require Docker

### "Python 3.12 not found"
- Phase 3 should install it
- If still missing: `sudo apt-get install -y python3.12 python3.12-venv python3.12-dev`

### "NautilusTrader compilation error"
- Usually takes 10-15 minutes to compile
- Make sure instance stays connected (SSH doesn't timeout)
- Check disk has >2GB free space

### "API credentials not working"
- Verify keys in .env file
- Make sure `BYBIT_API_KEY` and `BYBIT_API_SECRET` are not empty
- Check API key is active in BYBIT dashboard
- Verify API permissions include "Trading" and "Order Management"

---

## Pre-Deployment Checklist

Before running deployment:

- [ ] AWS t3.medium instance running in Singapore
- [ ] Can SSH to `ubuntu@13.228.94.51`
- [ ] Have BYBIT API keys ready
- [ ] Have $500 USDT in BYBIT spot wallet
- [ ] Understand kill switch will activate at -$100 loss
- [ ] Ready to monitor for first 24 hours

---

## Post-Deployment Checklist

After deployment:

- [ ] All 8 phases completed successfully
- [ ] API credentials added to .env file
- [ ] Strategy configuration verified
- [ ] Kill switch confirmed active
- [ ] Monitor script tested
- [ ] Emergency stop script tested
- [ ] Ready to start trading on testnet

---

## Support & Logs

Deployment logs saved to:
```
~/trading/logs/
~/trading/nautilus_trader/logs/
~/trading/emergency_stops.log
```

Check logs if anything fails:
```bash
cat ~/trading/deployment.log
tail -50 ~/trading/nautilus_trader/logs/*.log
```

---

**Created**: January 27, 2026  
**Strategy**: Specialized Low-Capital OBI Market Maker v1.0  
**Capital**: $500 USDT  
**Kill Switch**: -$100 USD (-20% of capital)
