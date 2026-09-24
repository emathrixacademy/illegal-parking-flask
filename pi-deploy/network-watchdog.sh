#!/bin/bash
# Pings an outside host every 60s and rebuilds the network stack after repeated
# failures. Written for dhcpcd originally; Raspberry Pi OS Bookworm and newer use
# NetworkManager instead, so the network manager is now detected at runtime —
# hardcoding "systemctl restart dhcpcd" made this a no-op on newer images and the
# Pi never recovered from an outage on its own.

PING_TARGET="8.8.8.8"
MAX_FAILS=3
FAIL_COUNT=0

unit_exists() {
    systemctl list-unit-files --no-legend "$1" 2>/dev/null | grep -q .
}

# NOTHING here adds the 192.168.8.x alias by hand any more, and nothing should.
#
# A raw "ip addr add" on eth0 makes NetworkManager decide the interface is managed
# by somebody else: it logs 'connection-assumed, managed-type: external' and from
# that moment stops running DHCP on eth0 at all. Measured on the Pi — the watchdog
# re-added the alias at 14:42:53 and NM assumed the interface in the same second.
# Plugging in a LAN cable afterwards produced a link with no lease, no default
# route, and no way to reach CAM1 or the internet over the wire, which at the site
# looks exactly like a dead port.
#
# The alias now lives in the NetworkManager profile itself:
#
#   nmcli con mod "Wired connection 1" ipv4.method auto ipv4.addresses 192.168.8.100/24
#
# so NM hands out the DHCP lease AND the camera-subnet address together every time
# eth0 comes up, including after the restarts this script performs below.

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

            # The camera subnet comes back with the NetworkManager profile when the
            # service restarts — see the note at the top of this file for why adding
            # it by hand here would stop DHCP on eth0 outright.

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
