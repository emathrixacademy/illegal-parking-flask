# CLAUDE.md - Project Context for AI Assistants

## Known Stable Commit
**`4142a04`** (Sept 24, 2026) — deployed to the Pi and verified live: the app runs, the
Hailo does 37ms/frame, the tunnel builds itself, Railway shows the Pi online, and a real
LAN cable produces a DHCP lease alongside the camera subnet. Six failure modes fixed
that day, each of which had the system looking healthy while doing nothing useful:

1. The whole recovery loop sat inside the try block that starts cloudflared, so after a
   brownout — Pi boots before the ISP returns — the heartbeat, settings sync and tunnel
   retry never started at all, and the Pi stayed invisible until someone restarted it.
2. `ensure_tunnel_alive()` trusted `proc.poll()`, but cloudflared routinely outlives its
   own tunnel, so "Cloud Link Disconnected" was permanent. It probes the public URL now.
3. `Stream` froze its URL at construction, so a camera that changed DHCP lease was
   dialled at its old dead address forever no matter what the settings sync wrote.
4. Camera_3 was missing from `update_local_config`, so `CAM3_URL` never reached config.py
   and `c3` was None at boot with no later sync able to revive it.
5. The autopull cron compared `HEAD != origin/main`, which is also true when the Pi is
   *ahead*, so it restarted parking-detect every five minutes and dropped every vehicle's
   dwell timer with it.
6. The network watchdog re-added the camera subnet with `ip addr add`, which made
   NetworkManager stop running DHCP on eth0. See the eth0 warning further down.

Older fallback: `054ac42` (May 17, 2026). Revert only if the above turns out worse:
```bash
git reset --hard 4142a04     # or 054ac42
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

## Pi Network Reliability — REBUILT Sept 24, 2026

⚠️ **NEVER run `ip addr add` on eth0. Not from a service, not from a watchdog, not by
hand.** NetworkManager reacts by declaring the interface externally managed
(`connection-assumed, managed-type: external`) and from that moment **stops running
DHCP on eth0 entirely**. Plugging in a LAN cable then gives a link with the camera
alias and nothing else — no lease, no default route, no path to CAM1 or the internet.
At the site that is indistinguishable from a dead port, and it cost three cable tests
to find. `camera-subnet.service` is **disabled** for exactly this reason.

The camera subnet lives in the NetworkManager profile instead, alongside DHCP:

```
Wired connection 1   interface-name eth0
  ipv4.method      auto                ← DHCP lease from the site router
  ipv4.addresses   192.168.8.100/24    ← CAM2/CAM3, applied at the same time
  autoconnect      yes, priority 100   ← beats wifi for the default route
```

Verified with a real cable: lease `192.168.254.163` **and** `192.168.8.100/24` both on
eth0, default route via eth0 at metric 100 against wlan0's 600, NM state
`connected:Wired connection 1` with `managed-type: 'full'`.

Services enabled and proven to come up on their own across four reboots:
- `parking-detect` — the app; `Restart=always`
- `network-watchdog.sh` + `.service` — pings every 60s, restarts networking after 3 failures
- `parking-watchdog.sh` + `.service` — restarts parking-detect when `/ping` stops answering
  3 times; `Restart=always` cannot help a process that is wedged rather than exited
- `camera-subnet.service` — **disabled on purpose**, see the warning above
- `hailort.service` — **disabled on purpose**, it claims the Hailo device exclusively
- `dhcpcd-static.conf` — stale, this image is NetworkManager; kept for reference only

WiFi profiles saved: `Emathrix24` (priority 0), `CLIENT-GERKENT2.4G` and
`CLIENTGERKENT2.4G` (priority 10, both added because the leading hyphen in the client's
SSID could not be confirmed from a screenshot — only one will ever match). **WiFi cannot
reach CAM2/CAM3**: the 192.168.8.x alias is on eth0 and cannot be put on wlan0. The LAN
cable is mandatory for all three cameras.

## After Pi Reboot Checklist
Recovery is automatic — verified four times. Manual steps only if something is wrong:
1. Verify cameras: `ping -c1 192.168.8.2 && ping -c1 192.168.8.199 && ping -c1 192.168.1.3`
2. If CAM1 is unreachable its DHCP lease may have moved (it went .14 → .3 once already).
   `camera_recovery.py` sweeps the /24 in ~2.4s, proves a candidate by pulling a real
   frame, and publishes the new URL to Railway — so wait a few minutes before scanning
   by hand.
3. Check eth0 got both addresses: `ip -4 addr show eth0` — expect a DHCP lease **and**
   192.168.8.100/24. If the lease is missing, something ran `ip addr add`; see above.
4. `hailort.service` must stay **disabled** — the Python app manages the Hailo device
   directly. With it enabled, detection falls back to CPU at ~1s/frame (Hailo does 37ms).

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
- **No SMS arriving while everything else looks fine**: check the UniSMS balance before
  suspecting anything in this repo. Alerting runs entirely on Railway (`app.py` →
  `alerts.py`), never on the Pi, so a Pi restart or settings sync changes nothing.
  ```bash
  curl -s -u "$UNISMS_API_KEY:" https://unismsapi.com/api/account
  ```
  `/api/account` is the only endpoint that distinguishes a good key (200) from a bad one
  (401) — `/api/sms` answers 404 to GET either way. On Sept 24 2026 the account was
  `active` with a valid key and `sms_credits: 0`, which fails silently: `_send_via_unisms`
  logs an error, returns False, and nothing surfaces on the dashboard. Also check
  `ALERT_CONFIG.sms_enabled` — it gates the send before the API is ever called.
- **Violations take 5 minutes to appear**: by design. `VIOLATION_TIME_THRESHOLD` is 300s,
  so a vehicle must block the zone that long before anything is recorded or texted. For a
  live demo, lower it through `/api/db_settings` and put it back afterwards.
- **Camera feed shows video but no red zone outline**: the zone is drawn onto the
  *processed* frame, so `gen_single()` falls back to the raw frame until detection has
  produced one. A few seconds at startup is normal; longer means detection is not running.
  Note `monitor.process()` returns early when a camera has no zone — no outline **and**
  no detection for that camera.
