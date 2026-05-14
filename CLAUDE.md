# CLAUDE.md - Project Context for AI Assistants

## Known Stable Commit
**`f45d5c1`** (May 12, 2026) — last verified fully working state. Includes: mobile responsive UI, landing page, password toggle, violation-only recordings, calendar redesign, tamper tuning, open redirect fix. Revert here if things break:
```bash
git reset --hard f45d5c1
```

## Architecture
- **Pi (edge)**: RTSP capture, Hailo-8L YOLOv8 detection, zone monitoring, violation recording (60s clips), Cloudflare tunnel
- **Railway (cloud)**: PostgreSQL, Cloudinary uploads, web dashboard, alerts, analytics
- **Cameras**: ONVIF-compatible, no auth on RTSP, subnet 192.168.8.x

## Route Structure
- `/` — Public landing page (unauthenticated visitors see system features)
- `/login` — Login page (with password toggle)
- `/dashboard` — Main dashboard (requires login)
- `/violations` — Violations timeline
- `/calendar` — Incident calendar with violation clips
- `/playback` — Redirects to `/calendar`
- `/settings` — System settings (Operator+)
- `/8f3c9a2d71b4e6c0f9d2a8b7c4e1` — Admin panel

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
- **No continuous recording** — recordings only happen per-violation (60s clips uploaded to Cloudinary)
- Tamper detection SSIM threshold is 0.25 with 30-min reference auto-refresh

## Railway
- URL: https://web-production-dbb23.up.railway.app
- API key header: `X-API-Key: dcgl-pi-secret-2026`
- Deploys automatically on push to `main`

## Common Issues
- **"Cloud Link Disconnected"**: Cloudflare tunnel URL changed — restart `parking-detect` service
- **Disk full**: Check `/tmp/violation_*.mp4` and `static/tamper/` — auto-cleanup runs every 30 min
- **Slow detection (~1s/frame)**: Hailo not working, fell back to CPU — check `/dev/hailo0` exists and `hailort.service` is stopped
- **config.py merge conflicts**: Never manually edit config.py on Pi — it's overwritten by settings sync
- **False tamper alerts**: SSIM reference frame goes stale — auto-refresh every 30 min handles this; threshold at 0.25 avoids false positives from lighting changes
