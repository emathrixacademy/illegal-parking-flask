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

> ⚠️ **Pi was re-imaged Sept 9, 2026.** Everything below the OS is gone and must be
> rebuilt — see "Post-Reset Rebuild" section.

**Image:** Raspberry Pi OS, pi-gen build `2026-06-18`. This is new enough that it uses
**NetworkManager + cloud-init**, NOT `dhcpcd`. The `pi-deploy/` scripts were written for
`dhcpcd` and are stale — use `pi-deploy/rebuild-after-reset.sh`, which detects both.

**Boot config as written by Imager** (`bootfs` partition: `user-data`, `network-config`):
```
hostname:   ADMIN          → mDNS name is ADMIN.local (NOT raspberrypi.local)
user:       admin / admin
SSH:        enabled (enable_ssh: true, ssh_pwauth: true)
wlan0:      SSID "Emathrix24", regulatory-domain PH
eth0:       dhcp4 true  ← the working path on the site LAN
```

⚠️ **The Pi has no WiFi it can actually reach.** `Emathrix24` is not the site network,
and it was never joined to any other AP. **Ethernet is mandatory** — plug it into the
site router (192.168.1.1) and it will pull a DHCP lease. To add a WiFi network later,
edit `network-config` on the `bootfs` partition with the SD card in a PC.

**Finding the Pi on the site LAN:** try `ADMIN.local` first (avahi-daemon is installed
via cloud-init `packages:`). Otherwise ping-sweep the subnet and look for Raspberry Pi
MAC OUIs: `b8:27:eb`, `dc:a6:32`, `e4:5f:01`, `28:cd:c1`, `d8:3a:dd`, `2c:cf:67`.

**Old pre-reset values (for reference only, no longer valid):** `192.168.1.15`, `admin` / `admin123`
The static IP is gone with the rest of the `pi-deploy/` config — the Pi takes a DHCP
lease until `rebuild-after-reset.sh site` is run.

## Post-Reset Rebuild — what the re-image wiped
Nothing below survived; all of it needs reinstalling before detection works again:
- Python venv + `requirements.txt` packages
- Hailo runtime (`hailort_4.23.0_arm64.deb`, in repo root)
- `parking-detect.service` and `cloudflared`
- `pi-deploy/` services: `camera-subnet`, `network-watchdog`, static-IP `dhcpcd.conf` block
- Autopull cron job (`~/autopull.sh`, every 5 min)
- `~/.dcgl_env` (vision key file)

Run `pi-deploy/rebuild-after-reset.sh` — it covers all of the above and supersedes
`setup_pi.sh` + `pi-deploy/DEPLOY.md`, which assume `dhcpcd`:
- `bash rebuild-after-reset.sh base` — anywhere with internet (apt, venv, Hailo, systemd, cron)
- `bash rebuild-after-reset.sh site` — only on the 192.168.1.x camera LAN (static IP, camera subnet, watchdog)
- `bash rebuild-after-reset.sh verify` — check what is installed

The `base` phase tolerates the code already being present (SCP/USB), so a private
repo with no token on the Pi is not a blocker.

## Camera Credentials & RTSP URLs
```
CAM1 (MAIN — TP-Link VIGI, 192.168.1.x subnet):
  IP:   192.168.1.3  (DHCP — may change after power loss, was 192.168.1.14 before)
  User: admin
  Pass: @Dm1n2026
  RTSP: rtsp://admin:%40Dm1n2026@192.168.1.3:554/stream1
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

## Pi Network Reliability (pi-deploy/) — DEPLOYED May 17, 2026
Services are **active and enabled** on the Pi. Files in `pi-deploy/` are the source of truth:
- `camera-subnet.service` — oneshot systemd unit, adds 192.168.8.100/24 on boot ✓
- `network-watchdog.sh` + `.service` — pings every 60s, restarts networking after 3 failures ✓
- `dhcpcd-static.conf` — static IP 192.168.1.15 for Pi ✓
- See `pi-deploy/DEPLOY.md` for full SSH deploy instructions (already done)

## After Pi Reboot Checklist
With pi-deploy services installed, recovery is automatic. Manual steps only needed if services fail:
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
    -d '{"CAM1_URL":"rtsp://admin:%40Dm1n2026@192.168.1.3:554/stream1","CAM2_URL":"rtsp://192.168.8.2:554/stream","CAM3_URL":"rtsp://192.168.8.199:554/stream"}'
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
- **"Cloud Link Disconnected"**: Cloudflare tunnel URL changed. As of Sept 9, 2026 the Pi
  heals this itself — `ensure_tunnel_alive()` in `server.py` restarts a dead cloudflared and
  re-posts the new URL within 30s. A manual `systemctl restart parking-detect` is only needed
  if it stays disconnected for several minutes. (`cloudlink.py` also drains cloudflared's
  stdout now; leaving that pipe unread was filling the OS buffer and silently killing tunnels.)
- **A camera stuck on "Reconnecting" while the others are fine**: was a bug, fixed Sept 9, 2026.
  Railway's `/api/camera_status` hardcoded `Camera_1`/`Camera_2` and dropped `Camera_3`, which
  the dashboard read as offline forever. It now passes through whatever the Pi reports.
- **Cameras drop after heavy rain and never come back on the dashboard**: the RTSP reader in
  `server.py` retries forever, so the cameras themselves recover. What breaks is the internet →
  tunnel → Railway path. See the two entries above.
- **Disk full**: Check `/tmp/violation_*.mp4` and `static/tamper/` — auto-cleanup runs every 30 min
- **Slow detection (~1s/frame)**: Hailo not working, fell back to CPU — check `/dev/hailo0` exists and `hailort.service` is stopped
- **config.py merge conflicts**: Never manually edit config.py on Pi — it's overwritten by settings sync
- **False tamper alerts**: SSIM reference frame goes stale — auto-refresh every 30 min handles this; threshold at 0.25 avoids false positives from lighting changes
