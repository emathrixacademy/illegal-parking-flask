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

# Where the code lives. Default is ~/illegal-parking, but if this script is being run
# from inside an existing copy of the project (SCP'd or USB-copied into a folder with
# a different name), use that copy. The site phase reads unit files out of
# $APP_DIR/pi-deploy, and a mismatched folder name made it die on the first cp.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/../server.py" ]; then
    APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    APP_DIR="$HOME/illegal-parking"
fi
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
    # "|| true" is load-bearing on a Pi that has no crontab yet: grep exits 1 on the
    # empty input, and under set -e + pipefail that killed the subshell before the
    # echo ran. The result was an EMPTY crontab piped in, autopull silently never
    # installed, and the script aborting before the sudoers step below.
    (crontab -l 2>/dev/null | grep -v autopull || true; echo "*/5 * * * * $HOME/autopull.sh") | crontab -

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

    say "[2/5] Network watchdog"
    cp "$APP_DIR/pi-deploy/network-watchdog.sh" "$HOME/network_watchdog.sh"
    chmod +x "$HOME/network_watchdog.sh"
    sudo cp "$APP_DIR/pi-deploy/network-watchdog.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now network-watchdog

    say "[3/5] App watchdog (restarts parking-detect when it wedges)"
    cp "$APP_DIR/pi-deploy/parking-watchdog.sh" "$HOME/parking_watchdog.sh"
    chmod +x "$HOME/parking_watchdog.sh"
    sudo cp "$APP_DIR/pi-deploy/parking-watchdog.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now parking-watchdog

    say "[4/5] Static IP 192.168.1.15"
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
        # The profile is NOT always called "Wired connection 1" — a cloud-init image
        # names it after the netplan profile it generated. Guessing the name meant
        # nmcli failed, set -e killed the script at step 3, and step 4 (which starts
        # parking-detect) never ran. Look up whatever profile actually owns eth0.
        ETH_CON="$(nmcli -t -f NAME,DEVICE con show 2>/dev/null | awk -F: '$2=="eth0"{print $1; exit}')"
        if [ -z "$ETH_CON" ]; then
            ETH_CON="$(nmcli -t -f NAME,TYPE con show 2>/dev/null | awk -F: '$2=="802-3-ethernet"{print $1; exit}')"
        fi
        if [ -z "$ETH_CON" ]; then
            echo "WARNING: no NetworkManager ethernet profile found — static IP skipped."
            echo "         Inspect with 'nmcli con show' and set it by hand."
        else
            echo "using NetworkManager profile: $ETH_CON"
            sudo nmcli con mod "$ETH_CON" \
                ipv4.addresses 192.168.1.15/24 \
                ipv4.gateway 192.168.1.1 \
                ipv4.dns "192.168.1.1 8.8.8.8" \
                ipv4.method manual
            # Ship default is autoconnect-priority -999 on ethernet against 0 on the
            # wifi profile, so NetworkManager hands the default route to wifi whenever
            # both are up. The cameras only exist on the wired side, so wired wins.
            sudo nmcli con mod "$ETH_CON" \
                connection.autoconnect yes \
                connection.autoconnect-priority 100
            echo "static IP staged — applies on reboot. Applying it now instead with"
            echo "'nmcli con up \"$ETH_CON\"' will drop your SSH session, because the"
            echo "Pi's address changes to 192.168.1.15 the moment it takes effect."
        fi
    fi

    say "[5/5] Enable and start services"
    sudo systemctl enable cloudflared 2>/dev/null || true
    sudo systemctl start parking-detect
    echo "Reboot recommended: sudo reboot"
}

# ---------------------------------------------------------------- verify

phase_verify() {
    say "Services"
    for s in parking-detect cloudflared camera-subnet network-watchdog parking-watchdog hailort; do
        # is-enabled prints "not-found" on stdout *and* exits non-zero, so the old
        # "|| echo" printed both and every missing unit took two confusing lines.
        state="$(systemctl is-enabled "$s" 2>/dev/null || true)"
        if [ -z "$state" ] || [ "$state" = "not-found" ]; then
            state="not installed"
        fi
        printf "%-20s %s\n" "$s" "$state"
    done
    say "Hailo device"
    [ -e /dev/hailo0 ] && echo "/dev/hailo0 present" || echo "/dev/hailo0 MISSING"

    say "Application"
    echo "APP_DIR: $APP_DIR"
    [ -f "$APP_DIR/server.py" ] && echo "server.py present" || echo "server.py MISSING"
    if [ -x "$APP_DIR/venv/bin/python" ]; then
        echo "venv present ($("$APP_DIR/venv/bin/python" --version 2>&1))"
        # These four are what the detector actually needs at import time; a venv that
        # exists but cannot import them still fails at boot with nothing in verify.
        for m in cv2 flask hailo_platform torch; do
            printf "  %-16s %s\n" "$m" \
                "$("$APP_DIR/venv/bin/python" -c "import $m" 2>/dev/null && echo OK || echo 'IMPORT FAILED')"
        done
    else
        echo "venv MISSING — run the base phase"
    fi
    command -v cloudflared >/dev/null && echo "cloudflared: $(cloudflared --version 2>&1 | head -1)" \
        || echo "cloudflared MISSING"

    say "Network"
    hostname -I
    printf "%-16s %s\n" "internet" \
        "$(ping -c1 -W2 8.8.8.8 >/dev/null 2>&1 && echo OK || echo 'no route')"
    printf "%-16s %s\n" "railway" \
        "$(curl -s -o /dev/null -m 10 -w '%{http_code}' https://web-production-dbb23.up.railway.app/ping 2>/dev/null || echo unreachable)"
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
