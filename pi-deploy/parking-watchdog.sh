#!/bin/bash
# Restart parking-detect when the app stops answering on localhost.
#
# systemd's Restart=always only helps when the process actually *exits*. A Python
# process that is alive but wedged — a stuck RTSP read holding a lock, a worker thread
# that died and took the detection loop with it — keeps systemd perfectly happy while
# the dashboard goes dark and stays dark until someone drives to the site.
#
# server.py has exposed /ping for exactly this check since the watchdog restart-loop
# fix, and its docstring names this script. Nothing was ever calling it.

PING_URL="http://127.0.0.1:5000/ping"
SERVICE="parking-detect"
MAX_FAILS=3
INTERVAL=60
STARTUP_GRACE=180
FAIL_COUNT=0

# Loading the Hailo models and opening three RTSP streams takes a while. Checking
# before that finishes would restart the app mid-startup, forever.
sleep "$STARTUP_GRACE"

while true; do
    if systemctl is-active --quiet "$SERVICE"; then
        if curl -fsS -m 10 -o /dev/null "$PING_URL"; then
            FAIL_COUNT=0
        else
            FAIL_COUNT=$((FAIL_COUNT + 1))
            logger -t parking-watchdog "No answer from $PING_URL ($FAIL_COUNT/$MAX_FAILS)"
            if [ "$FAIL_COUNT" -ge "$MAX_FAILS" ]; then
                logger -t parking-watchdog "App is wedged — restarting $SERVICE"
                systemctl restart "$SERVICE"
                FAIL_COUNT=0
                sleep "$STARTUP_GRACE"   # let it boot before judging it again
            fi
        fi
    else
        # Stopped or failed outright: that is systemd's own Restart=always to handle,
        # and piling a second restart on top of it only fights with the backoff.
        FAIL_COUNT=0
    fi
    sleep "$INTERVAL"
done
