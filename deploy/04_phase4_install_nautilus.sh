#!/bin/bash
# PHASE 4: Install NautilusTrader
# Installs Python, Rust, and NautilusTrader from source

set -e

echo "========================================================================="
echo "PHASE 4: INSTALL NAUTILUS TRADER"
echo "========================================================================="
echo ""

# Verify Python 3.12
echo "[1/6] Verifying Python 3.12..."
python3.12 --version || { echo "✗ Python 3.12 not found"; exit 1; }
echo "✓ Python 3.12 available"

# Install Rust
echo ""
echo "[2/6] Installing Rust (required for NautilusTrader)..."
if ! command -v rustc &> /dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source $HOME/.cargo/env
    echo "✓ Rust installed"
else
    echo "✓ Rust already installed: $(rustc --version)"
fi

# Create trading directory
echo ""
echo "[3/6] Setting up directories..."
mkdir -p ~/trading
cd ~/trading
echo "✓ Trading directory created: ~/trading"

# Clone NautilusTrader (using official repo)
echo ""
echo "[4/6] Cloning NautilusTrader repository..."
if [ ! -d ~/trading/nautilus_trader ]; then
    git clone https://github.com/nautechsystems/nautilus_trader.git
    echo "✓ Repository cloned"
else
    echo "⚠️ Repository already exists, updating..."
    cd ~/trading/nautilus_trader
    git pull
    echo "✓ Repository updated"
fi

cd ~/trading/nautilus_trader

# Create Python virtual environment
echo ""
echo "[5/6] Creating Python virtual environment..."
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
echo "✓ Virtual environment created"

# Install NautilusTrader
echo ""
echo "[6/6] Installing NautilusTrader (this may take 10-15 minutes)..."
pip install -e ".[all]" 2>&1 | tail -20
echo "✓ NautilusTrader installed"

# Verify installation
source ~/.venv/bin/activate 2>/dev/null || source .venv/bin/activate
python -c "import nautilus_trader; print('✓ NautilusTrader version:', nautilus_trader.__version__)"

# Install additional dependencies
echo ""
echo "Installing additional dependencies..."
pip install python-dotenv pandas numpy websockets aiohttp
echo "✓ Additional dependencies installed"

echo ""
echo "========================================================================="
echo "PHASE 4 COMPLETE - NAUTILUS TRADER INSTALLED"
echo "========================================================================="
echo ""
echo "Virtual environment: ~/trading/nautilus_trader/.venv"
echo "To activate: source ~/trading/nautilus_trader/.venv/bin/activate"
echo ""
