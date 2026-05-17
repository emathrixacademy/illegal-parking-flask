# CLAUDE.md - Project Context for AI Assistants

## Known Stable Commit
**`054ac42`** (May 17, 2026) — last verified fully working state. Includes everything from f45d5c1 plus: Cloudinary 14-day auto-cleanup, Cloud Media Uploads pause toggle, Cloud Media Management panel (manual selective delete), Philippine plate format validation, pi-deploy network reliability files. Revert here if things break:
```bash
git reset --hard 054ac42
```

## Architecture
- **Pi (edge)**: RTSP capture, Hailo-8L YOLOv8 detection, zone monitoring, violation recording (60s clips), Cloudflare tunnel
- **Railway (cloud)**: PostgreSQL, Cloudinary uploads, web dashboard, alerts, analytics
- **Cameras**: 3 total — 1 VIGI (192.168.1.x, requires auth) + 2 ONVIF (192.168.8.x, no auth)

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

## Camera Credentials & RTSP URLs
```
CAM1 (MAIN — TP-Link VIGI, 192.168.1.x subnet):
  IP:   192.168.1.3  (DHCP — may change after power loss, was 192.168.1.14 before)
  User: admin
  Pass: @Dm1n2026
  RTSP: rtsp://admin:%40Dm1n001@192.168.1.3:554/stream1
  Web:  https://192.168.1.3

CAM2 (ONVIF, 192.168.8.x subnet — no auth):
  IP:   192.168.8.2
  RTSP: rtsp://192.168.8.2:554/stream

CAM3 (ONVIF, 192.168.8.x subnet — no auth):
  IP:   192.168.8.199
  RTSP: rtsp://192.168.8.199:554/stream

ONVIF login (CAM2 & CAM3 admin panels only, NOT for RTSP):
  User: admin
  Pass: admin123
```

## Pi Network Reliability (pi-deploy/)
Deploy files in `pi-deploy/` to automate post-reboot recovery:
- `camera-subnet.service` — oneshot systemd unit, adds 192.168.8.100/24 on boot
- `network-watchdog.sh` + `.service` — pings every 60s, restarts networking after 3 failures
- `dhcpcd-static.conf` — static IP 192.168.1.15 for Pi
- See `pi-deploy/DEPLOY.md` for full SSH deploy instructions

## After Pi Reboot Checklist
If pi-deploy services are NOT yet installed:
1. Camera subnet is lost — re-add: `sudo ip addr add 192.168.8.100/24 dev eth0`
2. Verify cameras: `ping -c1 192.168.8.2 && ping -c1 192.168.8.199`
3. Verify VIGI cam: `ping -c1 192.168.1.3` (if unreachable, IP may have changed — scan: `for i in $(seq 1 254); do ping -c1 -W1 192.168.1.$i &>/dev/null && echo "192.168.1.$i UP"; done`)
4. Restart service: `sudo systemctl restart parking-detect`
5. `hailort.service` must stay **disabled** — the Python app manages the Hailo device directly

## Detection Models
- **yolov8s.hef** — Vehicle detection (COCO classes: person, bike, car, motorcycle, bus, truck)
- **cctv_ai.pt** — Custom trained model (11 classes: bike, car, chair, jeep, motorcycle, rock, trash, tree, tricycle, vendor, background)
- **cctv_ai_reserved.pt** — Previous model kept as backup
- CCTV AI classes use +100 offset to avoid ID conflicts with COCO (e.g., trash = class 6, stored as 106)
- `CCTV_AI_DETECTION_THRESHOLD = 0.1` (lowered from 0.2 for better trash detection; model mAP50 is ~0.30)
- Claude Vision garbage detection is **disabled** (Anthropic API credits exhausted)
- Scene-change tamper detection is **disabled** (false positives from outdoor lighting changes)

## Settings Sync & Railway Config
- Pi pulls settings from Railway every 30 seconds and overwrites local `config.py`
- **To persist camera URL changes**, you MUST update Railway's DB settings via API:
  ```bash
  curl -X POST "https://web-production-dbb23.up.railway.app/api/db_settings" \
    -H "X-API-Key: dcgl-pi-secret-2026" \
    -H "Content-Type: application/json" \
    -d '{"CAM1_URL":"rtsp://admin:%40Dm1n001@192.168.1.3:554/stream1","CAM2_URL":"rtsp://192.168.8.2:554/stream","CAM3_URL":"rtsp://192.168.8.199:554/stream"}'
  ```
- Manually editing `config.py` on Pi is temporary — it gets overwritten within 30 seconds
- Cloudflare tunnel URL changes on every service restart — Pi re-posts it to Railway automatically

## Key Constraints
- Hailo-8L supports only **one VDevice** — both models must share it with a shared lock
- `hailort.service` (systemd) grabs the device exclusively — keep it disabled
- `python3-hailort` is an apt package, symlinked into the venv (not pip-installable)
- CPU-only PyTorch is installed — never install nvidia/torch GPU packages (they waste 4+ GB)
- CAM2/CAM3 RTSP URLs have **no credentials** (Boa server) — adding `admin:admin123@` causes 401 errors
- CAM1 (VIGI) **requires** credentials — use URL-encoded `%40` for `@` in password
- **No continuous recording** — recordings only happen per-violation (60s clips uploaded to Cloudinary)
- Tamper detection: only obstruction and defocus checks active; scene-change disabled
- **Cloudinary plan**: Small PAYG ($29/month, 60 credits). Cloud name: `dwqgob3h9`
- **Cloudinary auto-cleanup**: Background thread deletes assets older than 14 days (runs every 6h)
- **Cloud uploads pause toggle**: Admin can pause/resume uploads via Settings to save credits
- **Cloud Media Management**: Admin can browse and selectively delete Cloudinary assets via Settings
- **PH plate validation**: OCR rejects non-Philippine plate formats (must be 2-3 letters + 3-4 digits)

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
