#!/usr/bin/env bash
# install.sh — One-shot setup for the Celestial Tracker on Raspberry Pi OS.
#
# Usage:  chmod +x install.sh && sudo ./install.sh
#
# What it does:
#   1. Installs system-level dependencies (pigpio, OpenCV native libs).
#   2. Creates a Python virtual-env and installs pip packages.
#   3. Enables and starts the pigpiod daemon.
#   4. Prints next-steps.

set -euo pipefail

echo "══════════════════════════════════════════════════════════"
echo "  Celestial Tracker — Installer"
echo "══════════════════════════════════════════════════════════"

# ── 1. System packages ────────────────────────────────────────────────────
echo "[1/4] Installing system packages …"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    pigpio python3-pigpio \
    libatlas-base-dev libhdf5-dev libharfbuzz-dev liblapack-dev \
    libjpeg-dev libpng-dev libtiff-dev \
    v4l-utils

# ── 2. Python virtual-env ─────────────────────────────────────────────────
VENV_DIR="$(dirname "$0")/venv"
echo "[2/4] Creating virtual-env at ${VENV_DIR} …"
python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip setuptools wheel -q
pip install -r "$(dirname "$0")/requirements.txt" -q

# ── 3. Enable pigpiod ─────────────────────────────────────────────────────
echo "[3/4] Enabling pigpiod service …"
systemctl enable pigpiod
systemctl start  pigpiod

# ── 4. Done ───────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  ✔  Installation complete!"
echo ""
echo "  Activate the venv:    source ${VENV_DIR}/bin/activate"
echo "  Start the tracker:    sudo python3 tracker.py"
echo "  Open the dashboard:   http://<PI_IP>:8080"
echo "══════════════════════════════════════════════════════════"
