#!/bin/bash
# Rebuild the illegal-parking Pi after a fresh Raspberry Pi OS re-image.
# Combines setup_pi.sh with the pi-deploy/DEPLOY.md network steps.
#
#   bash rebuild-after-reset.sh base    # run anywhere that has internet
#   bash rebuild-after-reset.sh site    # run ONLY on the 192.168.1.x camera LAN
#   bash rebuild-after-reset.sh verify  # check what is installed
#
# Split into two phases on purpose: the "site" phase pins the Pi to a static
# 192.168.1.15 and adds the 192.168.8.x camera subnet. Running that while the Pi
# is on a different WiFi will not help and can cut you off — so it is opt-in.

set -euo pipefail

REPO_URL="https://github.com/emathrixacademy/illegal-parking-flask.git"
APP_DIR="$HOME/illegal-parking"
PHASE="${1:-}"

say() { echo ""; echo "=== $* ==="; }

# ---------------------------------------------------------------- base phase

phase_base() {
    say "[1/8] System packages"
    sudo apt update && sudo apt upgrade -y
    sudo apt install -y git python3-pip python3-venv python3-opencv libopencv-dev

    say "[2/8] cloudflared"
    if ! command -v cloudflared >/dev/null; then
        curl -L -o /tmp/cloudflared.deb \
            https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
        sudo dpkg -i /tmp/cloudflared.deb || sudo apt-get install -f -y
        rm -f /tmp/cloudflared.deb
    else
        echo "already installed, skipping"
    fi

    say "[3/8] Repository"
    # The GitHub repo may be private, so a clone can fail without a token. If the
    # code was already put here another way (SCP from the dev machine, USB), use it.
    if [ -f "$APP_DIR/server.py" ]; then
        echo "code already present at $APP_DIR"
        git -C "$APP_DIR" pull 2>/dev/null || echo "(pull skipped — no credentials or not a repo)"
    else
        git clone "$REPO_URL" "$APP_DIR" || {
            echo "ERROR: clone failed. If the repo is private, copy the project to"
            echo "       $APP_DIR manually (scp/USB) and re-run this script."
            exit 1
        }
    fi
    cd "$APP_DIR"

    say "[4/8] Python venv"
    # --system-site-packages is required: python3-hailort is an apt package and
    # cannot be pip-installed, so the venv has to be able to see it.
    [ -d venv ] || python3 -m venv --system-site-packages venv
    # shellcheck disable=SC1091
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    pip install opencv-python-headless

    say "[5/8] Hailo runtime"
    if [ -f hailort_4.23.0_arm64.deb ]; then
        sudo dpkg -i hailort_4.23.0_arm64.deb || sudo apt-get install -f -y
        sudo apt install -y python3-hailort || echo "WARNING: python3-hailort not available via apt"
    else
        echo "WARNING: hailort_4.23.0_arm64.deb not in repo — install the Hailo runtime manually"
    fi
    # hailort.service grabs the Hailo device exclusively; the app manages it directly.
    sudo systemctl disable hailort 2>/dev/null || true
    sudo systemctl stop hailort 2>/dev/null || true

    say "[6/8] Secrets file"
    ENV_FILE="$HOME/.dcgl_env"
    if [ ! -f "$ENV_FILE" ]; then
        echo "DCGL_VISION_KEY=" > "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        echo "created $ENV_FILE — add the vision key if Claude Vision is re-enabled"
    else
        echo "already exists, skipping"
    fi

    say "[7/8] parking-detect service"
    sudo tee /etc/systemd/system/parking-detect.service > /dev/null <<EOF
[Unit]
Description=Illegal Parking Detection Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python server.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$HOME/.dcgl_env

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable parking-detect.service

    say "[8/8] Autopull cron (every 5 min)"
    cat > "$HOME/autopull.sh" <<SCRIPT
#!/bin/bash
cd "$APP_DIR" || exit 0
git fetch origin main 2>/dev/null
LOCAL=\$(git rev-parse HEAD)
REMOTE=\$(git rev-parse origin/main)
if [ "\$LOCAL" != "\$REMOTE" ]; then
    git pull origin main
    source "$APP_DIR/venv/bin/activate"
    pip install -r requirements.txt --quiet
    sudo systemctl restart parking-detect
    echo "\$(date): Updated and restarted parking-detect" >> "$HOME/autopull.log"
fi
SCRIPT
    chmod +x "$HOME/autopull.sh"
    (crontab -l 2>/dev/null | grep -v autopull; echo "*/5 * * * * $HOME/autopull.sh") | crontab -

    sudo tee /etc/sudoers.d/parking-detect > /dev/null <<SUDOERS
$USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart parking-detect
SUDOERS

    say "Base phase complete"
    echo "Pi IP: $(hostname -I | awk '{print $1}')"
    echo ""
    echo "NOT started yet — the app needs the camera LAN. When the Pi is back on"
    echo "the site network, run:  bash rebuild-after-reset.sh site"
}

# ---------------------------------------------------------------- site phase

phase_site() {
    say "[1/4] Camera subnet service (192.168.8.100/24 on eth0)"
    sudo cp "$APP_DIR/pi-deploy/camera-subnet.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now camera-subnet

    say "[2/4] Network watchdog"
    cp "$APP_DIR/pi-deploy/network-watchdog.sh" "$HOME/network_watchdog.sh"
    chmod +x "$HOME/network_watchdog.sh"
    sudo cp "$APP_DIR/pi-deploy/network-watchdog.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now network-watchdog

    say "[3/4] Static IP 192.168.1.15"
    if [ -f /etc/dhcpcd.conf ]; then
        sudo cp /etc/dhcpcd.conf /etc/dhcpcd.conf.bak
        if ! grep -q "192.168.1.15/24" /etc/dhcpcd.conf; then
            echo '
# Static IP for Pi
interface eth0
static ip_address=192.168.1.15/24
static routers=192.168.1.1
static domain_name_servers=192.168.1.1 8.8.8.8' | sudo tee -a /etc/dhcpcd.conf
        fi
    else
        # Raspberry Pi OS Bookworm and newer use NetworkManager, not dhcpcd.
        echo "no /etc/dhcpcd.conf — this image uses NetworkManager. Applying via nmcli:"
        sudo nmcli con mod "Wired connection 1" \
            ipv4.addresses 192.168.1.15/24 \
            ipv4.gateway 192.168.1.1 \
            ipv4.dns "192.168.1.1 8.8.8.8" \
            ipv4.method manual
        echo "NOTE: network-watchdog.sh restarts dhcpcd, which does not exist here."
        echo "      Edit it to use 'systemctl restart NetworkManager' instead."
    fi

    say "[4/4] Enable and start services"
    sudo systemctl enable cloudflared 2>/dev/null || true
    sudo systemctl start parking-detect
    echo "Reboot recommended: sudo reboot"
}

# ---------------------------------------------------------------- verify

phase_verify() {
    say "Services"
    for s in parking-detect cloudflared camera-subnet network-watchdog hailort; do
        printf "%-20s %s\n" "$s" "$(systemctl is-enabled "$s" 2>/dev/null || echo 'not installed')"
    done
    say "Hailo device"
    [ -e /dev/hailo0 ] && echo "/dev/hailo0 present" || echo "/dev/hailo0 MISSING"
    say "Network"
    hostname -I
    say "Cameras"
    for ip in 192.168.1.3 192.168.8.2 192.168.8.199; do
        printf "%-16s %s\n" "$ip" "$(ping -c1 -W1 "$ip" >/dev/null 2>&1 && echo REACHABLE || echo 'no reply')"
    done
    say "Autopull"
    crontab -l 2>/dev/null | grep autopull || echo "cron entry MISSING"
}

case "$PHASE" in
    base)   phase_base ;;
    site)   phase_site ;;
    verify) phase_verify ;;
    *)      echo "usage: bash $0 {base|site|verify}"; exit 1 ;;
esac
