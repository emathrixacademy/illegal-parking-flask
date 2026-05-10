# DECONGESTILAGUNA - Illegal Parking Detection System

An AI-powered illegal parking detection system using YOLOv8 object detection with a distributed edge-cloud architecture. Monitors parking zones via CCTV cameras, detects vehicles, tracks parking duration, and automatically captures violations with fine calculation.

**Technological University of the Philippines - Manila | March 2026**

## Table of Contents

- [Features](#features)
- [System Architecture](#system-architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [Default Credentials](#default-credentials)
- [API Documentation](#api-documentation)
- [Database Schema](#database-schema)
- [Project Structure](#project-structure)
- [Tech Stack](#tech-stack)
- [Contributors](#contributors)

## Features

### Core Features (1-12)
- **Dual Camera Monitoring** - Two RTSP camera streams (1280x720)
- **Real-time Object Detection** - YOLOv8 with Hailo AI accelerator (CPU fallback)
- **Vehicle Tracking** - ByteTrackLite algorithm for persistent vehicle ID tracking
- **Polygon Zone Detection** - Configurable parking zones per camera
- **Violation Detection** - Time-based violation triggering with configurable threshold
- **Cloud-Edge Architecture** - Raspberry Pi for detection, Railway for storage/analytics
- **Cloudflare Tunnel** - Public HTTPS URL without port forwarding
- **Image Caching** - 200-item LRU cache with 5-minute TTL
- **Analytics Dashboard** - Real-time violation statistics and fine collection tracking
- **Fine Mapping** - Configurable fines by vehicle type (CAR, MOTORCYCLE)
- **Enforcement Tracking** - Mark violations as paid/enforced
- **PDF Report Generation** - Filterable reports with daily/weekly/monthly breakdown (ReportLab)

### Feature 13: OCR + Playback System
- **License Plate Recognition** - EasyOCR-based offline plate text extraction from violation frames
- **Plate Search** - Search violations by plate number across all records
- **Continuous Recording** - Records RTSP streams to 1-hour MP4 segments on USB HDD
- **7-Day Retention** - Automatic cleanup of recordings older than 7 days
- **Video Playback** - Browse and play back recorded segments by camera and date

### Feature 14: Tampering Detection
- **Obstruction Detection** - Detects if the camera lens is covered (brightness analysis)
- **Defocus/Spray Detection** - Detects blur via Laplacian variance
- **Scene Change Detection** - SSIM-based comparison against reference frame
- **Tamper Event Logging** - Saves last good frame and logs to database
- **Dashboard Alerts** - Real-time tamper alert banner on the dashboard

### Feature 15: Digital Zoom
- **Mouse Wheel Zoom** - Scroll to zoom in/out on live camera feeds
- **Click-Drag Pan** - Pan around when zoomed in
- **Pinch-to-Zoom** - Mobile touch gesture support
- **Zoom Controls** - On-screen +/- buttons with zoom level indicator
- **Double-Click Reset** - Quick reset to 1x zoom

### Feature 16: Health Monitoring & Remote Heartbeat
- **CPU Temperature** - Raspberry Pi thermal monitoring
- **Memory Usage** - RAM utilization tracking
- **Disk Usage** - Storage capacity monitoring (system + recording HDD)
- **Camera Heartbeat** - RTSP stream connectivity check
- **Dashboard Widget** - Color-coded health status (green/yellow/red)
- **Alert Triggers** - Critical alerts for high temp (>80C), low RAM (>90%), offline cameras
- **Pi Heartbeat** - Pi sends heartbeat every 30s to Railway with uptime and CPU temp
- **Remote Pi Status** - Dashboard shows live Pi status: Online (green), Stale (yellow), Offline (red)
- **Last Seen Tracking** - Shows exact time since last heartbeat (e.g., "Pi: Online (12s ago) | 48.2°C | Up:3h25m")

### Feature 17: Email / SMS Alerts
- **HTML Email Notifications** - Professional styled HTML emails via Gmail SMTP matching the PDF report design (orange header, dark summary box, detail tables, branded footer)
- **Violation Email Alerts** - Includes camera, vehicle type, duration, fine amount, confidence score, tracker ID, enforcement status, and optional photo attachment
- **SMTP Test Emails** - Styled HTML test email with system status table (connection, auth, delivery verification)
- **SMS Notifications** - TextBee REST API via spare Android phone
- **Configurable Recipients** - Multiple email and phone number recipients
- **Cooldown Logic** - Configurable minimum time between alerts per camera (default 300s)
- **Alert History** - Logged in database with success/failure tracking
- **Web Configuration** - Settings page UI for configuring email/SMS with "Test SMTP" button

### Feature 18: Multi-User Access
- **Role-Based Access Control** - Admin, Operator, Viewer roles
- **bcrypt Password Hashing** - Secure credential storage
- **Session Management** - 30-minute session timeout
- **User CRUD** - Admin can create, edit, deactivate, and delete users
- **Activity Logging** - All user actions logged with timestamp and IP
- **Protected Routes** - All pages require login; role-based route restrictions
- **User Management Page** - Full user administration interface

#### Role Permissions

| Feature | Admin | Operator | Viewer |
|---------|-------|----------|--------|
| View Dashboard | Yes | Yes | Yes |
| View Violations | Yes | Yes | Yes |
| View Playback | Yes | Yes | Yes |
| Change Settings | Yes | Yes | No |
| Configure Alerts | Yes | Yes | No |
| Test SMTP | Yes | Yes | No |
| Mark Enforced | Yes | Yes | No |
| Admin Panel | Yes | No | No |
| User Management | Yes | No | No |

### Feature 19: Progressive Web App (PWA) + Mobile-First UI
- **Installable PWA** - Add to home screen on mobile/desktop; standalone app mode
- **Web App Manifest** - App name, icons (192/512 SVG), theme color (#352070)
- **Service Worker v3** - Aggressive caching for offline-first experience
  - Precaches all HTML pages, static assets, CDN resources (Bootstrap, Chart.js)
  - Separate data cache for API responses
  - Network-first strategy for API calls and HTML pages with cache fallback
  - Cache-first strategy for static assets
  - Branded offline fallback page when no cache available
- **Background Sync** - Queued POST actions (mark enforced) auto-sync when connectivity returns
- **Offline Queue** - localStorage-based queue for offline mutations; auto-flushes on reconnect
- **Mobile-First Responsive Design** - 4 breakpoints:
  - Mobile (default): Single column, bottom navigation
  - Tablet (768px+): 2-column grids, larger nav icons
  - Desktop (1024px+): Sidebar navigation replaces bottom nav
  - Wide desktop (1400px+): Expanded content cards
- **Bottom Navigation Bar** - Home, Violations, Playback, Settings (operator+), More menu
- **Slide-Up "More" Sheet** - User info, Admin Panel, User Management (admin), Logout
- **Desktop Sidebar** - Auto-switches at 1024px+ with full navigation links
- **Clean Light Theme** - CSS variables: `--bg: #f0f2f7`, `--card: #ffffff`, `--primary: #352070`
- **Deep Purple Accent Color** - Professional purple/indigo palette with Inter font family

### Feature 20: Notification Bell System
- **Real-time Notification Bell** - Bell icon in dashboard status bar with badge count
- **Violation Alerts** - Rings on every new violation detected via polling
- **Dropdown Panel** - Click bell to view notification history (up to 50 entries)
- **Mute/Unmute Toggle** - Silence notification sounds with persistent preference
- **Web Audio API Sound** - Dual-tone bell sound (880Hz + 1100Hz) for new violations
- **Bell Ring Animation** - CSS keyframe animation on new notifications
- **localStorage Persistence** - Notifications and mute state persist across page loads
- **Violation Banner** - Auto-dismissing banner overlay for new violations

### Feature 22: SMS Notification Module (NOTIF)
- **Standalone SMS Panel** - Separate Flask app for admin SMS decisions (`NOTIF/app.py`)
- **UniSMS Integration** - Sends SMS via UniSMS API (`https://unismsapi.com/api/sms`)
- **Reservation Decisions** - Approve, Reject, or Needs Appearance actions with SMS notification
- **SMS Preview** - Live preview of generated message with 160-character limit
- **Appearance Scheduling** - Date/time picker for "Needs Appearance" decisions
- **Clean Admin UI** - Light theme card-based design (`NOTIF/admin.html`)
- **Pre-configured Requests** - Sample lab reservation request data for testing
- **API Key**: Set via `UNISMS_SECRET_KEY` environment variable

### Feature 21: Performance Optimization + Caching
- **Server-Side Pagination** - Violations API supports `?page=1&per_page=30` (newest first)
  - DISTINCT ON (camera, tracker_id) for unique violations
  - Returns `{ items, page, per_page, total, total_pages }`
  - Pagination controls in admin panel (prev/next buttons, page info, total count)
- **Global API Cache (localStorage)** - `ApiCache` utility in base template
  - Configurable TTL per endpoint (default 30s)
  - `ApiCache.fetch(url, { ttl, cacheKey, force })` for cached GET requests
  - Auto-cleanup of expired entries; graceful handling of storage full
  - Clears all cache on reconnect (`online` event)
- **HTTP Cache Headers** - `Cache-Control: private, max-age=15` on violations API
- **Offline Queue** - `OfflineQueue` utility for POST actions when offline
  - Queues failed mutations in localStorage
  - Auto-flushes pending actions when connectivity restored
  - Background sync via service worker message passing

## System Architecture

```
                    +-------------------+
                    |   Web Browser     |
                    |   (PWA Client)    |
                    +--------+----------+
                             |
                    HTTPS (Railway / localhost)
                             |
              +--------------+--------------+
              |     Railway Cloud (app.py)   |
              |  Flask + Gunicorn            |
              |  - Dashboard UI              |
              |  - User Auth (bcrypt)        |
              |  - Violations API (paginated)|
              |  - Alert System (HTML email) |
              |  - PDF Reports (ReportLab)   |
              |  - Analytics Blueprint       |
              |  - Settings Management       |
              +------+------------------+----+
                     |                  |
          HTTPS (Cloudflare)     PostgreSQL (Railway)
                     |                  |
              +------+------+    +------+--------+
              | Raspberry Pi |    |   Database    |
              | (server.py)  |    | - violations  |
              | - YOLOv8     |    | - config      |
              | - ByteTrack  |    | - plate_records|
              | - EasyOCR    |    | - tamper_events|
              | - Recording  |    | - alert_log   |
              | - Tampering  |    | - users       |
              | - Health Mon |    | - activity_log|
              +------+-------+   +---------------+
                     |
              +------+------+
              |   Cameras   |
              | Camera 1    |
              | Camera 2    |
              +------+------+
                     |
              USB HDD (/mnt/recording)
              1-hour MP4 segments
              7-day retention
```

## Requirements

### Software
- Python 3.10+
- PostgreSQL database (Railway/Render hosted)

### Python Packages
```
flask
flask-login
bcrypt
opencv-python-headless
numpy
gunicorn
requests
sqlalchemy
psycopg2-binary
reportlab
psutil
scikit-image
easyocr
```

### Hardware (Edge Device)
- Raspberry Pi 5 (8GB RAM)
- Hailo-8L AI Accelerator (13 TOPS) - optional, CPU fallback available
- 4TB USB HDD for continuous recording
- 4MP varifocal camera + IR illuminator for plate recognition
- PoE IP cameras (2x) for parking zone monitoring

## Installation

### 1. Clone Repository
```bash
git clone https://github.com/emathrixacademy/illegal-parking-flask.git
cd illegal-parking-flask
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Set Environment Variables

**For Railway (Cloud Server):**
```bash
export POSTGRES_URL="postgresql://user:password@host:port/database"
export PORT=5000
export SECRET_KEY="your-secret-key"
```

**For Raspberry Pi:**
```bash
export RAILWAY_API_URL="https://web-production-dbb23.up.railway.app"
export RASPI_IP="192.168.8.101"
export PORT=5000
```

### 4. Database Setup
Tables are created automatically on first run:
- `violations` - Violation records with camera, tracker, label, duration, fine, enforcement
- `config` - System settings (key-value JSONB store)
- `plate_records` - License plate OCR results (Feature 13)
- `tamper_events` - Camera tampering events with JSONB details (Feature 14)
- `alert_log` - Email/SMS alert history with success/failure (Feature 17)
- `users` - User accounts with roles and bcrypt hashes (Feature 18)
- `activity_log` - User activity audit trail with IP tracking (Feature 18)

### 5. Configure Cameras
Edit `config.py` with your camera RTSP URLs:
```python
CAM1_URL = "rtsp://192.168.8.2:554/stream"
CAM2_URL = "rtsp://192.168.8.199:554/stream"
```

### 6. Configure Parking Zones
Run the interactive zone selector to define parking polygons:
```bash
python zone_selector.py
```

## Running the Application

### Railway Cloud Server (NO DOCKER)
Railway deploys using `Procfile`:
```
web: gunicorn -b 0.0.0.0:$PORT app:app --timeout 120 --workers 2
```

Push to GitHub and Railway auto-deploys.

### Local Development
```bash
python app.py
```
Server starts at `http://127.0.0.1:5000`

### Raspberry Pi Edge Device
```bash
python server.py
```

This starts:
- Flask server on port 5000
- Cloudflare tunnel for public URL
- Camera stream connections
- Parking monitor + detection pipeline
- Continuous recorders (1-hour MP4 segments)
- Tamper detection (obstruction, defocus, scene change)
- Health monitoring (CPU, RAM, disk, cameras)

### Automated Pi Setup (Fresh SD Card)
For a freshly installed Raspberry Pi OS (64-bit), run:
```bash
git clone https://github.com/emathrixacademy/illegal-parking-flask.git illegal-parking
cd illegal-parking
bash setup_pi.sh
```

The `setup_pi.sh` script automatically:
1. Updates the system and installs dependencies (git, python3, opencv, cloudflared)
2. Enables SSH
3. Creates a Python virtual environment and installs packages
4. Installs the Hailo runtime (if .deb is present)
5. Creates a `parking-detect` systemd service for auto-start on boot
6. Sets up **auto-pull from GitHub** (checks every 5 minutes, pulls new code and restarts the service)
7. Configures passwordless sudo for service restart

### Auto-Start on Boot (systemd)
The setup script creates `/etc/systemd/system/parking-detect.service`:
```ini
[Unit]
Description=Illegal Parking Detection Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=admin
WorkingDirectory=/home/admin/illegal-parking
ExecStart=/home/admin/illegal-parking/venv/bin/python server.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable parking-detect
sudo systemctl start parking-detect
sudo systemctl status parking-detect
journalctl -u parking-detect -f  # View logs
```

### Auto-Pull from GitHub
The Pi checks GitHub for new commits every 5 minutes via cron. When changes are detected:
1. Pulls the latest code
2. Installs any new dependencies
3. Restarts the `parking-detect` service

This means you can push code changes from your development PC and the Pi will automatically update within 5 minutes. Updates are logged to `~/autopull.log`.

### Network Configuration
The Pi requires two network addresses:
- **`192.168.1.x`** (DHCP) — for internet access via the site router
- **`192.168.8.101`** (static) — for communicating with cameras on the Hikvision switch

To add the second IP permanently:
```bash
echo -e "\n[Network]\nAddress=192.168.8.101/24" | sudo tee -a /etc/systemd/network/10-eth0.network
sudo ip addr add 192.168.8.101/24 dev eth0  # Apply immediately
```

## Default Credentials

### Web Dashboard
After first deployment, login with:
```
Username: admin
Password: admin2026
```
**Change this immediately after first login via User Management.**

### Raspberry Pi SSH Access
```
Host:     192.168.1.15 (DHCP — may change, scan subnet if unreachable)
Username: admin
Password: admin123
Port:     22
```

**Troubleshooting SSH access:**
1. Pi must be connected to the router via ethernet cable
2. If IP changed, scan the subnet: `for i in $(seq 1 254); do ping -c1 -W1 192.168.1.$i &>/dev/null && echo "192.168.1.$i UP"; done`
3. Try SSH on each discovered IP: `ssh admin@<ip>`
4. If password was changed, reset via monitor+keyboard or SD card `cmdline.txt` method

## API Documentation

### Authentication Routes
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/login` | GET/POST | Login page and form submission |
| `/logout` | GET | Logout and clear session |

### UI Routes (all require login)
| Endpoint | Method | Role | Description |
|----------|--------|------|-------------|
| `/` | GET | Any | Main dashboard (live feeds, analytics, notifications) |
| `/violations` | GET | Any | Violations timeline with image zoom |
| `/calendar` | GET | Any | Incident heatmap calendar with drill-down |
| `/playback` | GET | Any | Video playback + plate search |
| `/settings` | GET | Operator+ | Settings, alert config, SMTP test |
| `/8f3c9a2d71b4e6c0f9d2a8b7c4e1` | GET | Admin | Admin panel (violations table, fines) |
| `/user-management` | GET | Admin | User CRUD + activity log |

### PWA Routes
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/sw.js` | GET | Service worker (JavaScript) |
| `/static/manifest.json` | GET | PWA web app manifest |
| `/ping` | GET | Health check (returns "pong") |

### User Management API (Admin only)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/users` | GET | List all users |
| `/api/users` | POST | Create new user |
| `/api/users/<id>` | PUT | Update user |
| `/api/users/<id>` | DELETE | Delete user |
| `/api/activity_log` | GET | User activity audit trail |

### Alert API (Operator+)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/alert_config` | GET | Get alert configuration |
| `/api/alert_config` | POST | Save alert configuration |
| `/api/test_smtp` | POST | Send styled HTML test email |
| `/api/alert_log` | GET | Alert sending history (last 100) |

### Violations API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/violations_list` | GET | Paginated violations (`?page=1&per_page=30`) |
| `/api/mark_enforced` | POST | Mark violations as enforced (Operator+) |
| `/api/db_violations_count` | GET | Total violation count |
| `/api/violation_counts` | GET | Violations grouped by vehicle type |
| `/api/violation_stats` | GET | Aggregate statistics (total, avg confidence, fines, etc.) |

### Fine Management API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/fine_map` | GET | Get fine amounts by vehicle type |
| `/api/set_fine_map` | POST | Update fine map (Operator+) |

### Camera & Events API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/camera_status` | GET | Camera online status (proxied from Pi) |
| `/api/upload_event` | POST | Receive violation event from Pi (base64 image) |
| `/api/events` | GET | List events (`?date=YYYY-MM-DD` or `?dates_only=1`) |
| `/api/image_from_db` | GET | Serve violation image from local storage |
| `/api/proxy_image` | GET | Cached image proxy (5-min TTL, 200-item LRU) |
| `/api/list_images` | GET | Raw image list from Pi |
| `/video_feed_c1` | GET | MJPEG stream proxy for Camera 1 |
| `/video_feed_c2` | GET | MJPEG stream proxy for Camera 2 |

### Playback API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/playback/dates` | GET | Available recording dates |
| `/api/playback/segments` | GET | Segments for date (`?camera=...&date=...`) |
| `/api/playback/stream` | GET | Stream MP4 segment (HTTP range support) |

### Plate Search API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/plates/search` | GET | Search by plate number (`?q=ABC1234`) |

### Tamper Detection API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tamper_event` | POST | Report tampering event (from Pi) |
| `/api/tamper_events` | GET | List tamper events (`?unresolved=1`) |
| `/api/tamper_events/<id>/resolve` | POST | Mark tamper event resolved (Operator+) |

### Calendar & Incidents API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/calendar` | GET | Incident heatmap by date/month (`?month=YYYY-MM`) |
| `/api/calendar/details` | GET | Incident breakdown for date (`?date=YYYY-MM-DD`) |
| `/api/recent_incidents` | GET | Recent incidents for notification dropdown |
| `/api/pending_review_count` | GET | Count of incidents pending review |
| `/api/review_incident` | POST | Submit review decision for incident |
| `/api/model_performance` | GET | YOLOv8 & OCR accuracy metrics |

### Detection Control API (proxied to Pi)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/detection_control` | GET | Get detection status (enabled/disabled) |
| `/api/detection_control` | POST | Enable/disable detection, set confidence |
| `/api/detection_snapshot` | GET | Single annotated frame from AI detection |
| `/api/restart_detection` | POST | Force detection pipeline restart |

### System & Settings API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/system_health` | GET | Pi system metrics (CPU, RAM, disk) |
| `/api/health_summary_email` | POST | Receive and email daily health report |
| `/api/settings` | GET | Get settings (Pi-first, DB fallback) |
| `/api/settings` | POST | Save settings to DB + sync to Pi |
| `/api/db_settings` | GET | Database-only settings |
| `/api/db_settings` | POST | Save to database only |
| `/api/set_pi_url` | POST | Store Pi's Cloudflare tunnel URL |
| `/api/get_pi_url` | GET | Retrieve stored Pi URL |
| `/api/heartbeat` | POST | Receive Pi heartbeat (uptime, CPU temp) |
| `/api/pi_status` | GET | Pi online/stale/offline status with last seen |

### Report Generation
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/generate_report` | GET | Generate PDF report (`?period=day\|week\|month\|custom&date=...&vehicle_type=...&camera=...`) |

## Database Schema

### violations
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Unique identifier |
| camera | VARCHAR(32) | Camera identifier |
| tracker_id | INTEGER | Vehicle tracker ID |
| label | VARCHAR(32) | Vehicle type (CAR, MOTORCYCLE) |
| timestamp | TIMESTAMP | Detection time |
| image_path | TEXT | Path to violation image |
| confidence_score | REAL | Detection confidence |
| duration_minutes | REAL | Parking duration in minutes |
| fine_amount | REAL | Calculated fine (pesos) |
| barangay | VARCHAR(128) | Location barangay |
| enforced | BOOLEAN | Enforcement status |

### config
| Column | Type | Description |
|--------|------|-------------|
| key | VARCHAR(128) PK | Setting key |
| value | JSONB | Setting value |
| updated_at | TIMESTAMP | Last update time |

### plate_records
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Unique identifier |
| violation_id | INTEGER (FK) | References violations.id |
| plate_number | VARCHAR(20) | Detected plate text |
| confidence | REAL | OCR confidence score |
| plate_image_path | TEXT | Path to cropped plate image |
| camera | VARCHAR(32) | Camera identifier |
| timestamp | TIMESTAMP | Detection time |

### tamper_events
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Unique identifier |
| camera | VARCHAR(32) | Camera identifier |
| tamper_type | VARCHAR(32) | OBSTRUCTION, DEFOCUS, SCENE_CHANGE |
| details | JSONB | Detection metrics |
| last_good_frame_path | TEXT | Path to last good frame |
| timestamp | TIMESTAMP | Event time |
| resolved | BOOLEAN | Whether resolved |

### alert_log
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Unique identifier |
| alert_type | VARCHAR(16) | email or sms |
| camera | VARCHAR(32) | Camera identifier |
| vehicle_type | VARCHAR(32) | Vehicle type |
| success | BOOLEAN | Send success |
| error_message | TEXT | Error details if failed |
| timestamp | TIMESTAMP | Send time |

### users
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Unique identifier |
| username | VARCHAR(64) UNIQUE | Login username |
| password_hash | VARCHAR(256) | bcrypt hash |
| role | VARCHAR(16) | admin, operator, viewer |
| display_name | VARCHAR(128) | Display name |
| email | VARCHAR(128) | Email address |
| is_active | BOOLEAN | Account active status |
| created_at | TIMESTAMP | Creation time |
| last_login | TIMESTAMP | Last login time |

### activity_log
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL (PK) | Unique identifier |
| user_id | INTEGER (FK) | References users.id |
| action | VARCHAR(64) | Action performed |
| details | TEXT | Additional details |
| ip_address | VARCHAR(45) | Client IP |
| timestamp | TIMESTAMP | Action time |

## Project Structure

```
illegal-parking/
├── app.py                      # Cloud Flask server (main application)
├── server.py                   # Raspberry Pi edge server (detection + recording)
├── db.py                       # Database access layer (PostgreSQL)
├── auth.py                     # Authentication & RBAC (bcrypt, sessions)
├── alerts.py                   # Email/SMS alert system (HTML templates)
├── analytics.py                # Analytics Blueprint (stats, PDF reports)
├── admin_config.py             # Admin utilities (fines, paginated violations)
├── config.py                   # Static configuration (cameras, zones, thresholds)
├── app_detect.py               # YOLOv8/Hailo detection engine
├── ocr_module.py               # License plate OCR (EasyOCR)
├── recorder.py                 # Continuous 1-hour MP4 recording
├── tamper_detect.py            # Camera tampering detection (SSIM)
├── health_monitor.py           # System health monitoring (psutil)
├── zone_selector.py            # Interactive parking zone polygon editor
├── claude_vision.py            # Cloud vision analysis for plate reading
├── cloudlink.py                # Cloudflare Tunnel helper
├── requirements.txt            # Python dependencies
├── Procfile                    # Railway deployment (gunicorn)
├── setup_pi.sh                 # Automated Pi 5 setup (deps, service, auto-pull)
├── templates/
│   ├── base.html               # Master template (mobile-first, PWA, clean light theme)
│   ├── index.html              # Dashboard (live feeds, analytics, notification bell)
│   ├── login.html              # Login page (standalone, PWA-enabled)
│   ├── violations.html         # Violations timeline with image zoom
│   ├── playback.html           # Video playback + plate search
│   ├── calendar.html           # Incident heatmap calendar with drill-down
│   ├── settings.html           # Settings + alert config + SMTP test
│   ├── admin.html              # Admin panel (paginated violations + fines)
│   ├── user_management.html    # User CRUD + activity log
│   ├── admin_login.html        # Legacy admin login
│   └── history.html            # Placeholder for future history features
├── static/
│   ├── manifest.json           # PWA manifest (installable app)
│   ├── sw.js                   # Service worker v3 (offline-first + background sync)
│   ├── icons/
│   │   ├── icon-192.svg        # PWA icon 192x192
│   │   └── icon-512.svg        # PWA icon 512x512
│   ├── js/
│   │   └── zoom.js             # Digital zoom module (mouse + touch)
│   ├── events/                 # Violation images (cloud storage)
│   ├── violations/             # Violation images (Pi local)
│   └── tamper/                 # Tamper event images (Pi local)
├── NOTIF/
│   ├── app.py                  # SMS notification Flask app (UniSMS integration)
│   ├── admin.html              # SMS admin panel UI (reservation decisions)
│   └── Running on.txt          # Local server URL and API key reference
└── models/
    ├── yolov8s.hef             # Hailo-optimized YOLOv8s model (INT8 quantized)
    ├── cctv_ai.hef             # CCTV AI garbage detection model (Hailo)
    ├── cctv_ai.pt              # CCTV AI model (PyTorch, CPU fallback)
    ├── cctv_ai.onnx            # CCTV AI model (ONNX format)
    └── bytetrack.yaml          # ByteTrack tracker configuration
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Edge Device | Raspberry Pi 5 (8GB RAM) |
| AI Accelerator | Hailo-8L (13 TOPS) |
| Detection Model | YOLOv8s (.hef INT8 quantized) |
| Vehicle Tracking | ByteTrackLite |
| OCR | EasyOCR (offline, CPU) |
| Backend | Flask + Gunicorn |
| Database | PostgreSQL (Railway) |
| Frontend | Bootstrap 5.3.2, Vanilla JS, Chart.js |
| PWA | Service Worker v3, Web App Manifest |
| UI Framework | Mobile-first responsive, clean light theme (Inter font, purple accents) |
| Authentication | bcrypt + Flask sessions |
| Email Alerts | SMTP (Gmail) with HTML templates |
| SMS Alerts | TextBee REST API + UniSMS API |
| Tunnel | Cloudflare (cloudflared) |
| PDF Reports | ReportLab |
| Health Monitoring | psutil |
| Tampering Detection | OpenCV + scikit-image (SSIM) |
| Caching | localStorage API cache + Service Worker cache |
| Offline Support | Background Sync + Offline Queue |

## Security

- bcrypt password hashing with salting
- SQL parameterized queries (psycopg2) - no SQL injection
- Path traversal protection (`os.path.normpath`, `..` blocked)
- Session timeout (30 minutes)
- Role-based route protection with decorators
- CORS headers for cross-origin API access
- Hidden admin panel route (`/8f3c9a2d71b4e6c0f9d2a8b7c4e1`)

**Important:** Change the default admin password (`admin2026`) immediately after first deployment.

## Contributors

- Joseph Santander
- Carlito Tagarro
- Florante Sangrenes

---

### NOTIF SMS Module API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Admin SMS decision panel UI |
| `/api/requests` | GET | List all reservation requests |
| `/api/requests/<id>/decision` | POST | Save admin decision (approved/rejected/needs-appearance) |
| `/send-sms` | POST | Send SMS via UniSMS API |

**Environment Variable:** `UNISMS_SECRET_KEY` - UniSMS API key for SMS delivery

To run the NOTIF module:
```bash
cd NOTIF
export UNISMS_SECRET_KEY="sk_df5338f6-e788-4396-88b8-20b91b2aa26e"
python app.py
```

## Troubleshooting Guide

### Dashboard shows "Cloud Link Disconnected"

The Pi is not connected or server.py is not running.

**Step 1 — Find the Pi on the network:**
```bash
# From any machine on the same router
for i in $(seq 1 254); do ping -c1 -W1 192.168.1.$i &>/dev/null && echo "192.168.1.$i UP"; done

# Try SSH on each discovered IP
ssh admin@<ip>
# Password: admin123
```

**Step 2 — Check if server.py is running:**
```bash
ps aux | grep server.py
sudo systemctl status parking-detect
```

**Step 3 — If not running, start it:**
```bash
sudo systemctl start parking-detect
# Or manually:
cd ~/illegal-parking
source venv/bin/activate
python3 server.py &
```

### Cameras show "OFFLINE" (server running but no video)

**Step 1 — Check camera subnet reachability:**
```bash
ping -c 2 192.168.8.2
ping -c 2 192.168.8.199
```

If unreachable, the second IP on eth0 is missing:
```bash
sudo ip addr add 192.168.8.100/24 dev eth0
# Verify
ping -c 2 192.168.8.2
```

**Step 2 — Verify RTSP streams work:**
```bash
ffprobe -v error -show_streams "rtsp://192.168.8.2:554/stream" 2>&1 | head -5
```

Should show `codec_name=h264`. If it shows 401 Unauthorized, the camera password changed. These cameras (Boa server) do NOT require credentials — the correct URLs are:
```
CAM1_URL = "rtsp://192.168.8.2:554/stream"
CAM2_URL = "rtsp://192.168.8.199:554/stream"
```

**Step 3 — Verify config.py on the Pi:**
```bash
cat ~/illegal-parking/config.py
```

Must contain all of these (no duplicates):
```python
MODEL_PATH = "models/yolov8s.hef"
CAM1_URL = "rtsp://192.168.8.2:554/stream"
CAM2_URL = "rtsp://192.168.8.199:554/stream"
DETECTION_THRESHOLD = 0.3
VIOLATION_TIME_THRESHOLD = 100
REPEAT_CAPTURE_INTERVAL = 60
PARKING_ZONES = {"Camera_1": [[249, 242], [255, 404], [654, 426], [443, 261]], "Camera_2": [[46, 437], [453, 253], [664, 259], [678, 438]]}
```

If config.py is missing values, copy from the repo or add manually. Then restart:
```bash
sudo systemctl restart parking-detect
```

### Port 5000 already in use

A previous server.py instance didn't shut down cleanly:
```bash
fuser -k 5000/tcp
sleep 2
sudo systemctl restart parking-detect
```

### SSH password rejected

The Pi username is `admin` (not `set-admin`). If the password `admin123` doesn't work:
1. Connect a monitor + keyboard to the Pi
2. Login directly and reset: `sudo passwd admin`
3. Or pull the SD card, append `init=/bin/sh` to `cmdline.txt`, boot, run `passwd admin`, remove `init=/bin/sh`, reboot

### Pi IP address changed (DHCP)

The Pi gets its IP via DHCP from the router and it may change. Known IPs:
- `192.168.1.15` (confirmed May 2026)
- `192.168.1.6` (old, now assigned to PC)

To find the current IP, scan the subnet (see Step 1 above) or check the router admin page at `http://192.168.1.1`.

### config.py out of sync with repo

`config.py` contains camera-specific settings that may differ between dev and Pi. If `git pull` doesn't fix it:
```bash
cat ~/illegal-parking/config.py
```
Compare with the values listed above and edit with `nano ~/illegal-parking/config.py`.

### Quick Fix (90% of issues — run this first)

Most problems are caused by the Pi rebooting and losing the camera subnet IP. SSH into the Pi and run these 3 commands:

```bash
sudo ip addr add 192.168.8.100/24 dev eth0
fuser -k 5000/tcp 2>/dev/null; sleep 2
sudo systemctl restart parking-detect
```

Wait 30 seconds, then hard refresh the Railway dashboard (Ctrl+Shift+R). Cameras should be back online.

### After Pi reboot (cameras OFFLINE, Cloud Link Disconnected)

This is the most common issue. The Pi loses the `192.168.8.x` secondary IP on reboot because `dhcpcd` and `systemd-networkd` conflict.

```bash
# 1. SSH into Pi
ssh admin@192.168.1.15
# Password: admin123

# 2. Re-add camera subnet
sudo ip addr add 192.168.8.100/24 dev eth0

# 3. Verify cameras are reachable
ping -c 1 192.168.8.2
ping -c 1 192.168.8.199

# 4. Restart the detection service
sudo systemctl restart parking-detect

# 5. Verify it's running
journalctl -u parking-detect --no-pager | tail -10
```

The Cloudflare tunnel takes ~30 seconds to establish after restart. The Railway dashboard will auto-detect the new tunnel URL.

### Recovery checklist (full restart from scratch)

Only needed if the quick fix above doesn't work.

1. SSH into Pi: `ssh admin@192.168.1.15` (scan subnet if IP changed)
2. Add camera subnet: `sudo ip addr add 192.168.8.100/24 dev eth0`
3. Verify cameras: `ping -c1 192.168.8.2 && ping -c1 192.168.8.199`
4. Verify config: `cat ~/illegal-parking/config.py` (must have all 7 settings, no duplicates — see above)
5. Kill stale processes: `fuser -k 5000/tcp`
6. Start service: `sudo systemctl restart parking-detect`
7. Check logs: `journalctl -u parking-detect -f`
8. Wait 30 seconds for Cloudflare tunnel
9. Hard refresh dashboard: `https://web-production-dbb23.up.railway.app` (Ctrl+Shift+R)

### Common mistakes to avoid

- **Do NOT add credentials to RTSP URLs** — these cameras (Boa server) have no authentication. Using `admin:admin123@` will cause 401 errors.
- **Do NOT edit config.py by appending** — always overwrite or you get duplicates that cause `AttributeError`.
- **Do NOT run `python3 server.py &` if systemd is managing it** — use `sudo systemctl restart parking-detect` instead, or you'll get "Port 5000 already in use".
- **Do NOT assume the Pi IP is the same** — it uses DHCP. If SSH fails, scan the subnet first.

## Known Stable Commit

**Commit `81cc5c4`** (May 10, 2026) — fully working and verified in production.

To revert to this known-good state:
```bash
git reset --hard 81cc5c4
```

What works at this commit:
- Hailo-8L hardware acceleration (shared VDevice + shared lock for both models)
- Cloudflare tunnel auto-start and Railway URL registration
- Tamper images uploaded to Cloudinary (no local accumulation)
- Auto-cleanup of `/tmp` violation videos and old tamper images
- Camera date/time sync via ONVIF
- CPU-only PyTorch fallback (no nvidia/triton bloat)
- Pi SSH: `admin` / `admin123`

## Changelog

### May 10, 2026 - Hailo Fix, Disk Cleanup & Cloudinary Tamper Upload
- **Hailo Acceleration Restored**: Installed `python3-hailort` + `hailort-pcie-driver`, symlinked to venv
- **Shared VDevice**: Both parking and CCTV AI detectors share one `VDevice` with a shared lock (Hailo-8L only supports one)
- **Disk Cleanup**: Removed nvidia/torch/triton (4.3 GB saved), reinstalled CPU-only PyTorch (148 MB)
- **Tamper to Cloudinary**: Tamper images now sent as base64 to Railway and uploaded to Cloudinary instead of saved locally
- **Auto-Cleanup Thread**: Background thread removes tamper images and `/tmp` violation videos older than 1 hour
- **Config Fix**: Resolved merge conflict markers in `config.py` that crashed Railway deployment
- **Camera Date Sync**: Updated both cameras to correct date via ONVIF
- **Pi SSH Password**: Updated from `project123` to `admin123`

### April 27, 2026 - Remote Monitoring & Connectivity Fix
- **Pi Heartbeat System**: Pi sends heartbeat every 30s with uptime and CPU temp to Railway
- **Pi Status Dashboard**: Live Pi status indicator (Online/Stale/Offline) with last-seen time, CPU temp, and uptime
- **Railway URL Fix**: Updated default `RAILWAY_API_URL` to correct production deployment (`web-production-dbb23.up.railway.app`)

### April 26, 2026 - Edge Deployment & Reliability Fixes
- **Automated Pi Setup**: `setup_pi.sh` script for fresh SD card deployment (system deps, venv, Hailo runtime, systemd service, auto-pull)
- **Auto-Pull from GitHub**: Pi checks for new commits every 5 minutes and auto-restarts the service
- **CPU Detection Fallback**: Auto-downloads `yolov8s.pt` when Hailo `.hef` model is unavailable on CPU fallback
- **Tunnel URL Persistence**: Pi re-posts cloudflared tunnel URL to Railway every 60 seconds to survive Railway restarts
- **CORS Headers**: Added cross-origin headers to Pi server for cloud dashboard API calls
- **Stream Quality**: Increased MJPEG JPEG quality from 80% to 92% for clearer cloud dashboard video
- **Dual Network Support**: Pi configured with both `192.168.1.x` (internet) and `192.168.8.101` (camera subnet) addresses

### April 2026 - UI Redesign & Bug Fixes
- **UI Overhaul**: Complete redesign from dark theme (orange accents) to clean light theme (deep purple accents) inspired by modern admin panel design
  - New color scheme: `--primary: #352070`, `--bg: #f0f2f7`, `--card: #ffffff`
  - Inter font family for improved typography
  - White cards with subtle shadows, purple sidebar, clean tables
  - All 9 templates updated (base, index, violations, admin, playback, settings, user_management, login, admin_login)
- **Bug Fixes**:
  - Fixed database connection leaks in `analytics.py` (12 connections wrapped with try/finally)
  - Fixed missing try/finally in `db.py` (7 functions)
  - Added `IF NOT EXISTS` to 2 index creation statements in `db.py`
  - Added JSON validation to 13 API endpoints in `app.py` and `server.py` to prevent TypeError crashes
  - Fixed image cache empty dict edge case in `app.py`
- **NOTIF Module**: Added SMS notification panel for reservation decisions via UniSMS API

---

**DECONGESTILAGUNA** | Technological University of the Philippines - Manila | March 2026
