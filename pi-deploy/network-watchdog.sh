#!/bin/bash
PING_TARGET="8.8.8.8"
MAX_FAILS=3
FAIL_COUNT=0

while true; do
    if ! ping -c 1 -W 5 $PING_TARGET > /dev/null 2>&1; then
        FAIL_COUNT=$((FAIL_COUNT + 1))
        logger -t network-watchdog "Network check failed ($FAIL_COUNT/$MAX_FAILS)"
        if [ $FAIL_COUNT -ge $MAX_FAILS ]; then
            logger -t network-watchdog "Restarting networking stack..."
            systemctl restart dhcpcd
            sleep 10
            # Re-add camera subnet after dhcpcd restart
            ip addr add 192.168.8.100/24 dev eth0 2>/dev/null
            systemctl restart cloudflared
            FAIL_COUNT=0
            sleep 30
        fi
    else
        FAIL_COUNT=0
    fi
    sleep 60
done
