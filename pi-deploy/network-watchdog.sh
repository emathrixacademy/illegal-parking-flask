#!/bin/bash
# Pings an outside host every 60s and rebuilds the network stack after repeated
# failures. Written for dhcpcd originally; Raspberry Pi OS Bookworm and newer use
# NetworkManager instead, so the network manager is now detected at runtime —
# hardcoding "systemctl restart dhcpcd" made this a no-op on newer images and the
# Pi never recovered from an outage on its own.

PING_TARGET="8.8.8.8"
MAX_FAILS=3
FAIL_COUNT=0
CAMERA_SUBNET="192.168.8.100/24"
CAMERA_IFACE="eth0"

unit_exists() {
    systemctl list-unit-files --no-legend "$1" 2>/dev/null | grep -q .
}

restart_network() {
    for unit in dhcpcd.service NetworkManager.service systemd-networkd.service; do
        if unit_exists "$unit"; then
            logger -t network-watchdog "Restarting $unit"
            systemctl restart "$unit"
            return 0
        fi
    done
    logger -t network-watchdog "ERROR: no known network manager found"
    return 1
}

while true; do
    if ! ping -c 1 -W 5 "$PING_TARGET" > /dev/null 2>&1; then
        FAIL_COUNT=$((FAIL_COUNT + 1))
        logger -t network-watchdog "Network check failed ($FAIL_COUNT/$MAX_FAILS)"
        if [ $FAIL_COUNT -ge $MAX_FAILS ]; then
            logger -t network-watchdog "Restarting networking stack..."
            restart_network
            sleep 10

            # The secondary camera subnet is not persisted by the network manager,
            # so it has to be re-added after every restart.
            ip addr add "$CAMERA_SUBNET" dev "$CAMERA_IFACE" 2>/dev/null

            # The Cloudflare tunnel is owned by parking-detect (server.py spawns it
            # as a subprocess and re-publishes the URL when it dies), so it is only
            # restarted here if it genuinely runs as its own unit.
            if unit_exists cloudflared.service; then
                systemctl restart cloudflared
            fi

            FAIL_COUNT=0
            sleep 30
        fi
    else
        FAIL_COUNT=0
    fi
    sleep 60
done
