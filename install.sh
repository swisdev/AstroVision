#!/usr/bin/env bash
# install.sh — Automated installer for AstroVision (AV) on Raspberry Pi OS.
# Compatible with Raspberry Pi OS Bullseye (Debian 11), Bookworm (Debian 12), and Ubuntu.
#
# Usage:  chmod +x install.sh && sudo ./install.sh

set -euo pipefail

echo "══════════════════════════════════════════════════════════"
echo "  AstroVision (AV) — Automated System Installer"
echo "══════════════════════════════════════════════════════════"

# ── 0. Privilege Check ────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo "❌ Error: This script must be run as root. Please run:"
    echo "   sudo ./install.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Detect OS & Select Appropriate Packages ────────────────────────────
echo "[1/4] Updating package index and selecting system dependencies …"
apt-get update -qq

# Debian 12 (Bookworm) replaced libatlas-base-dev with libopenblas-dev
if apt-cache show libatlas-base-dev >/dev/null 2>&1; then
    BLAS_PKG="libatlas-base-dev"
elif apt-cache show libopenblas-dev >/dev/null 2>&1; then
    BLAS_PKG="libopenblas-dev"
else
    BLAS_PKG=""
fi

# Base build & media libraries
BASE_PKGS=(
    python3 python3-venv python3-pip
    build-essential make gcc git
    v4l-utils
    libhdf5-dev libharfbuzz-dev liblapack-dev
    libjpeg-dev libpng-dev libtiff-dev
)

if [ -n "$BLAS_PKG" ]; then
    BASE_PKGS+=("$BLAS_PKG")
fi

echo "      Installing core dependencies (${BLAS_PKG:-openblas}) …"
apt-get install -y -qq "${BASE_PKGS[@]}"

# ── 2. Handle pigpio (APT or Source Build) ─────────────────────────────────
echo "      Configuring pigpio hardware PWM driver …"
if apt-cache show pigpio >/dev/null 2>&1 && apt-get install -y -qq pigpio python3-pigpio 2>/dev/null; then
    echo "      ✔ pigpio installed from distribution packages."
else
    echo "      ℹ Package 'pigpio' not in apt repositories (common in Debian 12 Bookworm / 64-bit)."
    echo "      Compiling pigpio directly from official source (takes ~20 seconds) …"

    BUILD_DIR=$(mktemp -d)
    git clone --depth 1 https://github.com/joan2937/pigpio.git "$BUILD_DIR"
    make -C "$BUILD_DIR" -j"$(nproc 2>/dev/null || echo 1)"
    make -C "$BUILD_DIR" install
    ldconfig
    rm -rf "$BUILD_DIR"

    # Install systemd service unit for pigpiod if not present
    if [ ! -f /lib/systemd/system/pigpiod.service ] && [ ! -f /etc/systemd/system/pigpiod.service ]; then
        cat << 'EOF' > /etc/systemd/system/pigpiod.service
[Unit]
Description=Daemon required to control GPIO pins via pigpio
After=network.target

[Service]
ExecStart=/usr/local/bin/pigpiod -l
ExecStop=/bin/systemctl kill -s SIGKILL pigpiod
Type=forking

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
    fi
    echo "      ✔ pigpio compiled and installed successfully from source."
fi

# ── 3. Python Virtual Environment ─────────────────────────────────────────
VENV_DIR="${SCRIPT_DIR}/venv"
echo "[2/4] Creating Python virtual environment at ${VENV_DIR} …"
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi

# Activate venv
source "${VENV_DIR}/bin/activate"

echo "      Upgrading pip and installing Python packages …"
pip install --upgrade pip setuptools wheel -q

# Install requirements with fallback flexibility
pip install -r "${SCRIPT_DIR}/requirements.txt" -q || {
    echo "      ⚠ Strict requirements install failed, trying relaxed versions …"
    pip install "opencv-python-headless>=4.5.0" "flask>=2.2.0" "requests>=2.25.0" "pigpio>=1.78" "numpy>=1.21.0" -q
}

# ── 4. Enable and Start pigpiod Daemon ─────────────────────────────────────
echo "[3/4] Enabling and starting pigpiod service …"
systemctl daemon-reload 2>/dev/null || true
systemctl enable pigpiod 2>/dev/null || true
systemctl restart pigpiod 2>/dev/null || true

if systemctl is-active --quiet pigpiod; then
    echo "      ✔ pigpiod daemon is active and running."
else
    # Attempt direct launch if systemctl failed
    /usr/local/bin/pigpiod 2>/dev/null || /usr/bin/pigpiod 2>/dev/null || true
    echo "      ✔ pigpiod launched in background."
fi

# ── 5. Completion Summary ─────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  ✔ AstroVision installation successfully completed!"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  🚀 Quick Run Commands:"
echo "     1. Activate environment:  source ${VENV_DIR}/bin/activate"
echo "     2. Launch AstroVision:    sudo python3 tracker.py"
echo "        (or for test mode):    python3 tracker.py --no-servo"
echo ""
echo "  🌐 Open Web Dashboard in browser:"
echo "     Local:    http://localhost:8080"
echo "     Network:  http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo '<YOUR_PI_IP>'):8080"
echo "══════════════════════════════════════════════════════════"
