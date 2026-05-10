# CLAUDE.md - Project Context for AI Assistants

## Known Stable Commit
**`e6bb9fc`** (May 10, 2026) — last verified fully working state. Includes: normalized zones, timer bug fix, Cloudinary image/video storage, recordings DB, purple email theme, plate numbers in alerts, per-violation cooldown, matched calendar/dashboard counts. Revert here if things break:
```bash
git reset --hard e6bb9fc
```

## Architecture
- **Pi (edge)**: RTSP capture, Hailo-8L YOLOv8 detection, zone monitoring, violation recording, Cloudflare tunnel
- **Railway (cloud)**: PostgreSQL, Cloudinary uploads, web dashboard, alerts, analytics
- **Cameras**: ONVIF-compatible, no auth on RTSP, subnet 192.168.8.x

## Pi Access
```
Host: 192.168.1.15 (DHCP — scan subnet if unreachable)
User: admin
Pass: admin123
```

## Camera ONVIF Credentials
```
User: admin
Pass: admin123
CAM1: 192.168.8.2
CAM2: 192.168.8.199
```

## After Pi Reboot Checklist
1. Camera subnet is lost — re-add: `sudo ip addr add 192.168.8.100/24 dev eth0`
2. Verify cameras: `ping -c1 192.168.8.2 && ping -c1 192.168.8.199`
3. Restart service: `sudo systemctl restart parking-detect`
4. `hailort.service` must stay **disabled** — the Python app manages the Hailo device directly

## Key Constraints
- Hailo-8L supports only **one VDevice** — both models must share it with a shared lock
- `hailort.service` (systemd) grabs the device exclusively — keep it disabled
- `python3-hailort` is an apt package, symlinked into the venv (not pip-installable)
- CPU-only PyTorch is installed — never install nvidia/torch GPU packages (they waste 4+ GB)
- RTSP camera URLs have **no credentials** (Boa server) — adding `admin:admin123@` causes 401 errors
- Config sync: Pi pulls settings from Railway every 30 seconds and writes to local `config.py`

## Railway
- URL: https://web-production-dbb23.up.railway.app
- API key header: `X-API-Key: dcgl-pi-secret-2026`
- Deploys automatically on push to `main`

## Common Issues
- **"Cloud Link Disconnected"**: Cloudflare tunnel URL changed — restart `parking-detect` service
- **Disk full**: Check `/tmp/violation_*.mp4` and `static/tamper/` — auto-cleanup runs every 30 min
- **Slow detection (~1s/frame)**: Hailo not working, fell back to CPU — check `/dev/hailo0` exists and `hailort.service` is stopped
- **config.py merge conflicts**: Never manually edit config.py on Pi — it's overwritten by settings sync
