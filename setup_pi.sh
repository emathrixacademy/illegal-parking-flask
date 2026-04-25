#!/bin/bash
# Setup script for Raspberry Pi 5 - DECONGESTILAGUNA illegal parking detection
# Run this on a freshly installed Raspberry Pi OS (64-bit)
# Usage: bash setup_pi.sh

set -e

echo "=== DECONGESTILAGUNA Pi Setup ==="

# 1. Update system
echo "[1/7] Updating system..."
sudo apt update && sudo apt upgrade -y

# 2. Install dependencies
echo "[2/7] Installing system dependencies..."
sudo apt install -y git python3-pip python3-venv python3-opencv libopencv-dev

# Install cloudflared (not in default repos)
echo "[2b/7] Installing cloudflared..."
curl -L -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i /tmp/cloudflared.deb || sudo apt-get install -f -y
rm /tmp/cloudflared.deb

# 3. Enable SSH
echo "[3/7] Enabling SSH..."
sudo systemctl enable ssh
sudo systemctl start ssh

# 4. Clone the repo
echo "[4/7] Cloning repository..."
cd ~
if [ -d "illegal-parking" ]; then
    cd illegal-parking && git pull
else
    git clone https://github.com/emathrixacademy/illegal-parking-flask.git illegal-parking
    cd illegal-parking
fi

# 5. Create virtual environment and install Python packages
echo "[5/7] Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install opencv-python-headless

# 6. Install Hailo runtime (if .deb exists in repo)
echo "[6/7] Installing Hailo runtime..."
if [ -f "hailort_4.23.0_arm64.deb" ]; then
    sudo dpkg -i hailort_4.23.0_arm64.deb || sudo apt-get install -f -y
    echo "Hailo runtime installed."
else
    echo "WARNING: Hailo .deb not found. You may need to install it manually."
fi

# 7. Create systemd service for auto-start
echo "[7/7] Creating systemd service..."
sudo tee /etc/systemd/system/parking-detect.service > /dev/null <<EOF
[Unit]
Description=Illegal Parking Detection Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/illegal-parking-flask
ExecStart=/home/$USER/illegal-parking-flask/venv/bin/python server.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable parking-detect.service

echo ""
echo "=== Setup Complete ==="
echo "Pi IP: $(hostname -I | awk '{print $1}')"
echo ""
echo "To start the detection server now:"
echo "  sudo systemctl start parking-detect"
echo ""
echo "To check status:"
echo "  sudo systemctl status parking-detect"
echo ""
echo "To view logs:"
echo "  journalctl -u parking-detect -f"
