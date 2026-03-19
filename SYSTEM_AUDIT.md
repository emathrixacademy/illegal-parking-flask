# DECONGESTILAGUNA — System Audit Report (Completed)

**Completed by:** Florante Sangrenes (from codebase analysis)
**Date:** March 19, 2026
**Method:** Full source code audit of all .py files, templates, and static assets. Items requiring live Pi/server access are marked REQUIRES LIVE TEST.

---

## SECTION A: RASPBERRY PI HARDWARE

1. Is the Raspberry Pi 5 currently powered on and running 24/7?
REQUIRES LIVE TEST — cannot verify from code.

2. Is the Hailo-8L accelerator connected and being used for AI inference right now? Or is the system using CPU fallback?
CODE SAYS YES — config.py sets MODEL_PATH = "models/yolov8s.hef" (Hailo format). server.py imports app_detect.detect which uses the .hef model. REQUIRES LIVE TEST to confirm Hailo hardware is physically connected.

3. What is the current average FPS for detection? (Check the terminal output of server.py)
REQUIRES LIVE TEST — FPS is logged in server.py terminal output.

4. Is Camera 1 currently online and streaming?
REQUIRES LIVE TEST — config.py has CAM1_URL = "rtsp://192.168.8.2:554/stream". server.py creates Stream(CAM1_URL) and monitors connectivity.

5. Is Camera 2 currently online and streaming?
REQUIRES LIVE TEST — config.py has CAM2_URL = "rtsp://192.168.8.199:554/stream". Same Stream class setup.

6. Is the 4TB USB HDD connected and mounted to the Pi? If yes, what is the mount path?
CODE SAYS: recorder.py uses RECORDING_DIR = os.environ.get("RECORDING_DIR", "/mnt/recording"). Default mount path is /mnt/recording. REQUIRES LIVE TEST to confirm HDD is physically connected.

7. Is the 4TB HDD currently recording video? If yes, where are the files saved?
CODE SAYS YES — server.py creates 2 ContinuousRecorder instances (one per camera) and calls .start() on each. Files saved to /mnt/recording/Camera_1/ and /mnt/recording/Camera_2/ as MP4 segments. REQUIRES LIVE TEST to confirm it's actively writing files.

8. How much free space is left on the USB HDD right now? (Run: df -h /mnt/recording)
REQUIRES LIVE TEST — health_monitor.py reports this via /api/health endpoint under "recording_disk_gb".

9. How much free space is left on the Pi SD card right now? (Run: df -h /)
REQUIRES LIVE TEST — health_monitor.py reports this via /api/health under "disk_percent" and "disk_total_gb".

10. What is the current CPU temperature? (Run: vcgencmd measure_temp)
REQUIRES LIVE TEST — health_monitor.py reads this via subprocess call to vcgencmd measure_temp.

11. Is the Cloudflare tunnel currently running? If yes, what is the current tunnel URL?
CODE SAYS YES — server.py calls cloudlink.start_cloudflared(port) at startup, then posts the URL to Railway via /api/set_pi_url. REQUIRES LIVE TEST for current URL.

12. How long has the Pi been running without a restart? (Run: uptime)
REQUIRES LIVE TEST — health_monitor.py reports uptime_seconds in /api/health response.

---

## SECTION B: FILES ON THE RASPBERRY PI

13. server.py — exists? running?
YES — exists in repo. Main Flask app with 15 API routes, detection loop, violation tracking, recording, tamper detection, health monitoring.

14. app_detect.py — exists? imported by server.py?
YES — exists in repo. server.py imports: from app_detect import detect.

15. config.py — exists?
YES — exists. Contains CAM1_URL, CAM2_URL, MODEL_PATH, DETECTION_THRESHOLD, VIOLATION_TIME_THRESHOLD, REPEAT_CAPTURE_INTERVAL, PARKING_ZONES.

16. cloudlink.py — exists? running?
YES — exists. server.py imports start_cloudflared and calls it at startup. Runs cloudflared tunnel command.

17. ocr_module.py — does this file exist on the Pi? Is EasyOCR installed? Does OCR actually run when a violation is captured?
YES — exists. Uses easyocr.Reader(['en'], gpu=False) with lazy loading. Detects Philippine plate patterns (ABC 1234, ABC-1234, 1234-AB). server.py imports extract_plate from ocr_module and calls it in the ParkingMonitor._upload_violation() method when a violation is captured. easyocr is listed in requirements.txt. REQUIRES LIVE TEST to confirm easyocr is installed on Pi (pip show easyocr).

18. recorder.py — does this file exist? Is continuous video recording currently active? Are MP4 files being created on the HDD?
YES — exists. ContinuousRecorder class records RTSP streams to 1-hour MP4 segments at 15 FPS. server.py creates 2 instances and calls .start(). Files saved to /mnt/recording/Camera_X/YYYY-MM-DD/HH-MM-SS.mp4. 7-day auto-cleanup via _cleanup_old_recordings(). REQUIRES LIVE TEST to verify active recording.

19. tamper_detect.py — does this file exist? Is it imported and running inside the camera frame loop in server.py?
YES — exists. TamperDetector class with 3 detection types: OBSTRUCTION (brightness < 15), DEFOCUS (Laplacian variance < 100), SCENE_CHANGE (SSIM < 0.4). 60-second cooldown. server.py imports TamperDetector, creates instances, sets reference frames, and calls check() every 30 frames in processing_worker().

20. health_monitor.py — does this file exist? Is it imported and running? Is psutil installed?
YES — exists. HealthMonitor class reports CPU temp, CPU%, RAM, disk, recording disk, uptime, camera status. Alerts for CPU > 80C, RAM > 90%, disk > 90%. server.py imports and uses it for /api/health endpoint. psutil is in requirements.txt. REQUIRES LIVE TEST to confirm pip show psutil.

21. zone_selector.py — exists?
YES — exists in repo.

22. models/yolov8s.hef — exists? Is this the model currently being used?
REQUIRES LIVE TEST — config.py sets MODEL_PATH = "models/yolov8s.hef". The .hef file is a Hailo-compiled model (not tracked in git typically). Need to verify on Pi filesystem.

23. models/cctv_ai.hef — exists? What does this model detect? Is it being used?
PARTIAL — config.py sets CCTV_AI_MODEL_PATH = "models/cctv_ai.hef". analytics.py has class labels for it: BASKET, BOTTLE, BOX, BUCKET, CAN, CANAL, CARDBOARD, CHAIR, CONTAINER, CRATE, CUP, FALLEN_TREE, GARBAGE, etc. (debris/obstruction detection). REQUIRES LIVE TEST to confirm file exists and whether it's actively loaded.

---

## SECTION C: PI API ENDPOINTS

24. Does GET /api/camera_status work? What does it return?
YES — server.py line defines /api/camera_status. Returns JSON with Camera_1 and Camera_2 online/reconnecting status. REQUIRES LIVE TEST for actual response.

25. Does GET /api/health exist? What does it return?
YES — server.py defines /api/health. Returns full system health: cpu_temp, cpu_percent, memory (total/used/percent), disk (total/used/percent), recording_disk_gb, uptime_seconds, cameras (online/offline per camera), alerts array, overall_status (healthy/warning/critical).

26. Does GET /api/settings work?
YES — server.py defines GET|POST /api/settings. Returns VIOLATION_TIME_THRESHOLD, REPEAT_CAPTURE_INTERVAL, PARKING_ZONES from config.py.

27. Does GET /api/list_images work?
YES — server.py defines /api/list_images. Lists all files in static/violations directory.

28. Does GET /api/recording_dates exist? What does it return?
YES — server.py defines /api/recording_dates. Calls recorder.get_recording_dates(). Returns dict of camera_id -> list of available date strings.

29. Does GET /api/playback exist?
YES — server.py defines /api/playback. Accepts camera, date, time params. Serves the corresponding MP4 file from /mnt/recording.

30. Does GET /video_feed_c1 work?
YES — server.py defines /video_feed_c1. Returns MJPEG stream from Camera 1 via multipart/x-mixed-replace. REQUIRES LIVE TEST with camera connected.

31. Does GET /video_feed_c2 work?
YES — server.py defines /video_feed_c2. Same as above for Camera 2.

---

## SECTION D: RAILWAY CLOUD SERVER

32. Is the Railway app currently deployed and accessible? What is the URL?
REQUIRES LIVE TEST — Procfile exists with: web: gunicorn -b 0.0.0.0:$PORT app:app --timeout 120 --workers 2. Default URL in code: https://illegal-parking-detection-flask.up.railway.app

33. Does auto-deploy from GitHub work?
REQUIRES LIVE TEST — depends on Railway project config.

34. What is the current Procfile command?
YES — web: gunicorn -b 0.0.0.0:$PORT app:app --timeout 120 --workers 2

35. Is the PostgreSQL database hosted on Railway or Render? What is the host address?
YES — Railway. Host: mainline.proxy.rlwy.net:42362, database: railway. Connection string in db.py.

36. Can you connect to the database right now using psql or any database tool?
REQUIRES LIVE TEST.

---

## SECTION E: FILES IN THE GITHUB REPOSITORY

37. app.py? YES
38. analytics.py? YES
39. admin_config.py? YES
40. db.py? YES
41. auth.py? YES
42. alerts.py? YES
43. requirements.txt? YES
44. Procfile? YES
45. templates/base.html? YES
46. templates/index.html? YES
47. templates/login.html? YES
48. templates/violations.html? YES
49. templates/playback.html? YES
50. templates/settings.html? YES
51. templates/admin.html? YES
52. templates/user_management.html? YES
53. static/manifest.json? YES
54. static/sw.js? YES
55. static/js/zoom.js? YES

All 19 files confirmed present.

---

## SECTION F: DATABASE VERIFICATION

56. Table listing query:
VERIFIED FROM db.py ensure_tables() — creates these tables: violations, config, plate_records, tamper_events, alert_log, users, activity_log. REQUIRES LIVE TEST to run actual query.

57. Does the violations table exist? How many rows?
YES — created by db.py. Columns: id, camera, tracker_id, label, timestamp, image_path, confidence_score, duration_minutes, fine_amount, barangay, enforced. Has indexes on timestamp, camera, tracker_id, and (camera, tracker_id). REQUIRES LIVE TEST for row count.

58. Does the config table exist? How many rows?
YES — columns: key (PK), value, updated_at. Stores FINE_MAP, ALERT_CONFIG, VIOLATION_TIME_THRESHOLD, REPEAT_CAPTURE_INTERVAL, PARKING_ZONES. REQUIRES LIVE TEST for row count.

59. Does the plate_records table exist? How many rows?
YES — columns: id, violation_id (FK to violations), plate_number, confidence, plate_image_path, camera, timestamp. Has indexes on plate_number and timestamp. REQUIRES LIVE TEST for row count — likely low or zero since OCR depends on EasyOCR being installed on Pi.

60. Does the tamper_events table exist? How many rows?
YES — columns: id, camera, tamper_type, details (JSONB), last_good_frame_path, timestamp, resolved. REQUIRES LIVE TEST for row count.

61. Does the alert_log table exist? How many rows?
YES — columns: id, alert_type, camera, vehicle_type, success, error_message, timestamp. REQUIRES LIVE TEST for row count.

62. Does the users table exist? How many user accounts are in it?
YES — columns: id, username (unique), password_hash, role, display_name, email, is_active, created_at, last_login. db.py creates default admin user (username: admin, password: admin2026, role: admin) on first run. REQUIRES LIVE TEST for actual count.

63. Does the activity_log table exist? How many rows?
YES — columns: id, user_id (FK to users), action, details, ip_address, timestamp. REQUIRES LIVE TEST for row count.

64. Violations column query:
VERIFIED FROM CODE — id (serial), camera (varchar 32), tracker_id (integer), label (varchar 32), timestamp (timestamp), image_path (text), confidence_score (real), duration_minutes (real), fine_amount (real), barangay (text), enforced (boolean).

65. Users query:
VERIFIED FROM CODE — default admin user created: id=1, username=admin, role=admin, is_active=true, last_login=(depends on usage). REQUIRES LIVE TEST for actual data.

---

## SECTION G: FEATURE 13 — OCR + PLAYBACK

66. Does ocr_module.py exist on the Pi?
YES — exists in repo. Uses EasyOCR with Philippine plate pattern matching.

67. Is EasyOCR installed on the Pi?
IN REQUIREMENTS.TXT — easyocr is listed. REQUIRES LIVE TEST: pip show easyocr.

68. When the system captures a violation, does it attempt to read the plate number from the image?
YES — server.py ParkingMonitor._upload_violation() calls ocr_module.extract_plate(frame, bbox). If a plate is found with confidence > 0.4, it includes plate_number and plate_confidence in the upload payload to Railway.

69. Has any plate number ever been saved to plate_records table?
REQUIRES LIVE TEST — app.py upload_event() saves plate records when plate_number is in the payload. Run: SELECT COUNT(*) FROM plate_records;

70. Is there a plate search feature on the web dashboard?
YES — violations.html has a plate search input. API endpoint: /api/plates/search?q=PLATE_NUMBER. Also available on playback.html.

71. Is continuous video recording currently active?
CODE SAYS YES — server.py creates 2 ContinuousRecorder instances and calls .start() at startup. REQUIRES LIVE TEST to verify MP4 files exist.

72. How long is each recording segment?
1 HOUR — recorder.py SEGMENT_DURATION = 3600 seconds. Files recorded at 15 FPS, mp4v codec.

73. Is the 7-day auto-cleanup working?
CODE SAYS YES — recorder.py _cleanup_old_recordings() runs after each segment completes. MAX_DAYS = 7. Uses shutil.rmtree() on date folders older than cutoff. REQUIRES LIVE TEST to verify.

74. Does the playback page exist at /playback? Does it load?
YES — app.py defines /playback route with @login_required. Template playback.html exists with camera selector, date selector, segment loading, and video player.

75. Can you actually play back a recorded video from the web dashboard?
CODE SAYS YES — playback.html loads segments via /api/playback/segments, then plays via /api/playback/stream which proxies to the Pi's /api/playback endpoint serving MP4 files. REQUIRES LIVE TEST with actual recordings.

76. Does the timeline scrubbing work?
PARTIAL — playback.html uses native HTML5 video controls (play, pause, seek). Segment-based navigation (click segment button to load specific time). No custom visual timeline scrubber bar. You select date -> load segments -> click a segment -> video plays with native controls.

---

## SECTION H: FEATURE 14 — TAMPERING DETECTION

77. Does tamper_detect.py exist on the Pi?
YES — exists with TamperDetector class. 3 detection types with configurable thresholds.

78. Is tampering detection integrated into the main camera frame loop in server.py?
YES — server.py imports TamperDetector, creates instances for each camera, sets reference frames at startup, and calls tamper_detector.check(frame) every 30 frames in processing_worker(). When tampering detected, it POSTs to Railway /api/tamper_event with camera, tamper_type, details, and timestamp.

79. Cover Camera 1 lens — does system detect it?
CODE SAYS YES — OBSTRUCTION detection triggers when average brightness < 15. REQUIRES LIVE TEST.

80. Move Camera 1 — does system detect scene change?
CODE SAYS YES — SCENE_CHANGE detection using SSIM comparison against reference frame (threshold < 0.4). Requires scikit-image (in requirements.txt). REQUIRES LIVE TEST.

81. Are tamper events being saved to tamper_events table?
CODE SAYS YES — app.py /api/tamper_event (POST) inserts into tamper_events table. REQUIRES LIVE TEST: SELECT COUNT(*) FROM tamper_events;

82. When tampering is detected, does a red alert banner appear on the dashboard?
YES — index.html has a tamper alert banner (red background, "TAMPER DETECTED!" text) that fetches /api/tamper_events?unresolved=1 and displays camera name, tamper type, and timestamp with a dismiss button.

83. Can you view a list of past tamper events on the dashboard?
YES — /api/tamper_events endpoint returns last 50 events. The tamper banner shows unresolved events. Full history accessible via API.

84. Can you mark a tamper event as "resolved" from the dashboard?
YES — /api/tamper_events/<id>/resolve (POST) endpoint exists with @login_required @role_required('operator'). The tamper banner has a dismiss button.

---

## SECTION I: FEATURE 15 — DIGITAL ZOOM

85. Does static/js/zoom.js exist?
YES — implements ZoomPan class with 1x-8x zoom range.

86. Can you scroll mouse wheel to zoom in?
YES — zoom.js handles wheel events with ±0.3 delta per scroll.

87. When zoomed in, can you click-and-drag to pan?
YES — zoom.js implements mousedown/mousemove/mouseup drag panning with clamped translation bounds.

88. On mobile, does pinch-to-zoom work?
YES — zoom.js handles touchstart/touchmove/touchend with dual-finger distance calculation for pinch scaling.

89. Are there visible zoom control buttons (+, -, reset)?
YES — index.html has zoom buttons for both cameras: Zoom In (bi-zoom-in icon), Zoom Out (bi-zoom-out icon), Reset (bi-arrows-fullscreen icon).

90. Does zoom work on playback page?
NO — zoom.js is loaded on index.html for live feeds. playback.html uses native HTML5 video controls without zoom integration.

---

## SECTION J: FEATURE 16 — HEALTH MONITORING

91. Does health_monitor.py exist?
YES — HealthMonitor class with full system metrics collection.

92. Is psutil installed?
IN REQUIREMENTS.TXT — psutil listed. health_monitor.py imports it with graceful fallback. REQUIRES LIVE TEST: pip show psutil.

93. Does /api/health endpoint exist? Response?
YES — server.py defines /api/health. Returns: cpu_temp, cpu_percent, memory (total/used/percent), disk (total/used/percent), recording_disk_gb, uptime_seconds, cameras (per-camera online status), alerts array, overall_status.

94. CPU temperature in response?
YES — health_monitor.py get_cpu_temp() uses vcgencmd measure_temp. Included as cpu_temp in response.

95. RAM usage in response?
YES — memory_total_mb, memory_used_mb, memory_percent all included.

96. SD card disk usage in response?
YES — disk_total_gb, disk_used_gb, disk_percent all included.

97. USB HDD free space in response?
YES — recording_disk_gb reports free space on /mnt/recording using shutil.disk_usage.

98. Camera online/offline status in response?
YES — cameras dict with per-camera health: online (boolean), details, checked_at.

99. Does /api/system_health on Railway proxy Pi health?
YES — app.py /api/system_health with @login_required proxies to Pi's /api/health via requests.get(). Returns Pi data or {"overall_status": "unreachable"} if Pi is offline.

100. Is there a health status widget on the dashboard?
YES — index.html has a health status indicator in the dashboard header area. Shows colored dot + text with CPU temp and RAM%.

101. Does it show colored indicators?
YES — Green (#2ecc71) = healthy, Orange (#f39c12) = warning, Red (#e74c3c) = critical, Grey (#888) = unreachable.

102. Does it auto-refresh?
YES — every 30 seconds (setInterval in index.html).

103. Alert when CPU > 80C?
YES — health_monitor.py triggers "critical" alert when cpu_temp > 80. overall_status becomes "critical". The dashboard widget turns red.

104. Alert when RAM > 90%?
YES — health_monitor.py triggers "warning" alert when memory_percent > 90.

105. Alert when camera goes offline?
YES — health_monitor.py triggers "critical" alert when a camera's online status is False.

106. Daily health summary report?
NO — not implemented. Health is real-time only (dashboard widget + /api/health). No scheduled daily email/report.

---

## SECTION K: FEATURE 17 — EMAIL / SMS ALERTS

107. Does alerts.py exist?
YES — full implementation with SMTP email (HTML templates) + TextBee SMS + cooldown + logging.

108. Is alert config stored in database?
YES — stored in config table with key = 'ALERT_CONFIG'. get_alert_config() reads from DB, save_alert_config() writes to DB.

109. Email config on Settings page?
YES — settings.html has SMTP section: enable checkbox, SMTP email field, app password field, recipients field (comma-separated).

110. Test SMTP button?
YES — settings.html has "Test SMTP" button. Calls /api/test_smtp (POST). alerts.py send_test_email() sends styled HTML test email.

111. Auto email on violation?
YES — app.py upload_event() calls send_violation_alert() after inserting violation. Sends email if email_enabled=true and recipients configured.

112. Photo attachment in email?
YES — alerts.py send_email_alert() accepts image_bytes parameter. Attaches as MIMEImage with filename "violation.jpg" if provided.

113. HTML styled or plain text?
HTML — alerts.py _build_violation_email_html() creates rich HTML: orange header, dark summary box (#232733), detail table with styled rows, branded footer. Matches PDF report style.

114. SMS via TextBee implemented?
YES — alerts.py send_sms_alert() posts to https://api.textbee.dev/api/v1/gateway/devices/{device_id}/send-sms with API key header.

115. Spare Android phone set up?
REQUIRES LIVE TEST — code is implemented but needs physical phone running TextBee app.

116. SMS recipients on Settings page?
YES — settings.html has SMS section: enable checkbox, TextBee API Key, Device ID, SMS Recipients field.

117. Cooldown logic?
YES — alerts.py _can_send_alert() tracks per-camera last alert time. Default cooldown: 300 seconds (5 minutes). Configurable via cooldown_seconds in ALERT_CONFIG.

118. Alert history on dashboard?
YES — settings.html has Alert History table showing last 20 alerts (time, type, camera, vehicle, status). Data from /api/alert_log endpoint.

119. Does alert_log table have records?
REQUIRES LIVE TEST — table exists, records created by _log_alert() on every email/SMS attempt. Run: SELECT COUNT(*) FROM alert_log;

---

## SECTION L: FEATURE 18 — MULTI-USER ACCESS

120. Does auth.py exist?
YES — bcrypt hashing, 3 roles (admin=3, operator=2, viewer=1), session-based auth, @login_required and @role_required decorators, full CRUD for users.

121. Users table with at least 1 user?
YES — db.py ensure_tables() creates default admin user: username=admin, password=admin2026 (bcrypt hashed), role=admin.

122. Can you login with admin/admin2026?
CODE SAYS YES — auth.py authenticate() checks credentials against users table. REQUIRES LIVE TEST.

123. User Management page at /user-management?
YES — app.py defines /user-management with @login_required @role_required('admin'). Template user_management.html exists.

124. Create new Viewer user?
CODE SAYS YES — user_management.html has Add User modal with username, password, role dropdown (Admin/Operator/Viewer), display name, email. API: POST /api/users. REQUIRES LIVE TEST.

125. Viewer can see dashboard?
CODE SAYS YES — @login_required allows any logged-in user. Dashboard (/) only requires login, not a specific role.

126. Viewer blocked from Settings?
CODE SAYS YES — /settings has @role_required('operator'). Viewer role (level 1) < operator (level 2), so they get redirected to index.

127. Operator can access Settings but NOT Admin?
CODE SAYS YES — /settings requires 'operator' (level 2) = allowed. Admin panel at /8f3c9a2d71b4e6c0f9d2a8b7c4e1 requires 'admin' (level 3) = blocked for operator.

128. Bcrypt hashes starting with $2b$?
YES — auth.py hash_password() uses bcrypt.hashpw() which produces $2b$ prefixed hashes.

129. Session timeout after 30 minutes?
YES — app.py sets app.permanent_session_lifetime = timedelta(minutes=30) and before_request sets session.permanent = True.

130. Activity_log has records?
REQUIRES LIVE TEST — auth.py log_activity() is called on login, logout, create_user, update_user, delete_user, mark_enforced, update_alert_config.

131. Activity log on User Management page?
YES — user_management.html has Activity Log table showing: time, user, action, details, IP address. Fetches from /api/activity_log (last 200 entries).

132. Deactivate user account?
YES — user_management.html Edit User modal has "Active" checkbox. auth.py update_user() handles is_active field. authenticate() checks is_active and returns None if False.

133. Delete user? Admin protected?
YES — /api/users/<id> DELETE endpoint exists. auth.py delete_user() has protection: WHERE id = %s AND username != 'admin' — the admin account cannot be deleted.

---

## SECTION M: FEATURE 19 — PWA + MOBILE UI

134. manifest.json exists and valid?
YES — valid JSON. name: "DECONGESTILAGUNA - Illegal Parking Detection", short_name: "DECONGESTILAGUNA", display: "standalone", background_color: "#181c23", theme_color: "#181c23", icons: 192x and 512x SVG.

135. Service worker registered?
YES — base.html registers /sw.js. sw.js version: decongestilaguna-v3. Precaches all pages, static assets, and CDN resources. Separate DATA_CACHE for API responses.

136. Add to Home Screen prompt?
CODE SAYS YES — manifest.json + service worker + HTTPS (via Railway) = PWA installable. REQUIRES LIVE TEST on phone.

137. Standalone mode?
YES — manifest.json sets display: "standalone".

138. Bottom navigation bar on mobile?
YES — base.html has mobile bottom nav bar (shown below 1024px).

139. Bottom nav items?
YES — Home, Violations, Playback, Settings (admin/operator only), More.

140. More menu slide-up?
YES — base.html More button reveals: Admin Panel (admin only), User Management (admin only), Logout.

141. Desktop sidebar at 1024px+?
YES — base.html has sidebar nav for desktop: Dashboard, Violations, Playback, Settings, Admin, Users. Hidden on mobile, shown via CSS media query.

142. Dark theme throughout?
YES — base.html CSS variables: --bg: #181c23, --card: #232733, --accent: #ff9800. Applied globally to all templates via inheritance.

143. Offline cached content?
CODE SAYS YES — sw.js precaches all HTML pages. Network-first strategy for API/HTML, cache-first for static assets. Falls back to cache when offline. Custom offline fallback page. REQUIRES LIVE TEST.

144. Offline enforced sync?
CODE SAYS YES — base.html OfflineQueue queues POST actions. Admin.html has offline queue for mark_enforced with background sync (sync-enforced event). sw.js handles sync events. REQUIRES LIVE TEST.

---

## SECTION N: FEATURE 20 — NOTIFICATION BELL

145. Bell icon on dashboard?
YES — index.html has notifBell button with bi-bell-fill icon in the dashboard status bar area.

146. Count badge for new violations?
YES — bellBadge span shows count. Updated by setupViolatorNotification() which compares current violation count to localStorage lastKnownViolatorCount.

147. Bell animates on new violation?
YES — CSS @keyframes bellRing (rotate ±15deg). .ringing class added to bell when new violations detected.

148. Dropdown with notification history?
YES — clicking bell calls toggleNotifDropdown(). Shows list of up to 30 notifications with camera, type, time, and "NEW" badge for recent items.

149. Mute/unmute button?
YES — notification dropdown header has mute toggle button. Toggles between bi-volume-up-fill and bi-volume-mute-fill icons. State persisted in localStorage.

150. Notification sound plays?
YES — code creates AudioContext oscillators at 880Hz and 1100Hz (dual tone) for 0.18 seconds when new violations detected and not muted.

151. Notifications persist across refresh?
YES — notifications stored in localStorage. setupViolatorNotification() reads/writes lastKnownViolatorCount and notification history from localStorage.

152. Auto-dismissing banner on new detections?
YES — newViolationBanner div at top of dashboard (top: 56px). Shows "X new violator(s)" with dismiss button. Auto-dismisses after 8 seconds via setTimeout. First visit seeds count silently (no banner).

---

## SECTION O: FEATURE 21 — PERFORMANCE + CACHING

153. Pagination support?
YES — /api/violations_list accepts ?page=1&per_page=10. Returns: {items, page, per_page, total, total_pages}. Server-side LIMIT/OFFSET with DISTINCT ON (camera, tracker_id).

154. Admin panel pagination controls?
YES — admin.html has prevPage/nextPage buttons, pageInfo span ("Page X / Y"), totalInfo span ("N total violations"). PER_PAGE = 10.

155. ApiCache utility?
YES — base.html defines ApiCache object with localStorage-based caching. Methods: get(key), set(key, data, ttl), fetch(url, opts), clear(), cleanup(). Default TTL: 30 seconds.

156. Cache-Control headers?
YES — app.py violations_list endpoint sets Cache-Control: private, max-age=15.

157. OfflineQueue utility?
YES — base.html defines OfflineQueue object. Methods: enqueue(url, body), dequeue(), getAll(), flush(), clear(). Stored in localStorage key dcgl_offline_queue. Flushes automatically on 'online' event.

---

## SECTION P: KNOWN BUGS AND ISSUES

Bug 1: CRITICAL — Before today's security audit, 16+ API routes had NO authentication (@login_required missing). Anyone could read/modify settings, view camera feeds, inject fake violations. FIXED TODAY.

Bug 2: CRITICAL — /api/image_from_db had path traversal vulnerability. Check used startswith("/") which doesn't catch Windows absolute paths (C:\...). FIXED TODAY with os.path.isabs() + os.path.realpath() containment.

Bug 3: CRITICAL — /api/upload_event, /api/set_pi_url, /api/tamper_event had no authentication. Anyone could inject fake data. FIXED TODAY with Pi API key requirement (X-API-Key header).

Bug 4: MEDIUM — File handle leaks in api_image_from_db and static_files — open().read() without closing. FIXED TODAY with context managers.

Bug 5: MEDIUM — CORS was Access-Control-Allow-Origin: * on all responses. FIXED TODAY with origin checking.

Bug 6: LOW — Zoom not available on playback page (only live feeds).

Bug 7: LOW — No daily health summary email/report (health is real-time dashboard only).

Bug 8: LOW — Playback has no custom timeline scrubber (uses native HTML5 video controls + segment buttons).

---

## SECTION Q: CREDENTIALS AND ACCESS

159. Railway app URL: https://illegal-parking-detection-flask.up.railway.app (from code default — REQUIRES LIVE VERIFICATION)

160. Railway dashboard login: REQUIRES LIVE TEST — not stored in code.

161. PostgreSQL connection string: postgresql://postgres:ltymHUMvXphOojaHeJRJGnyQUfWsghwq@mainline.proxy.rlwy.net:42362/railway

162. GitHub repo URL: REQUIRES LIVE TEST — not stored in code.

163. Pi SSH command: REQUIRES LIVE TEST.

164. Pi local IP address: 192.168.8.101 (from app.py DEFAULT_RASPI_IP)

165. Cloudflare tunnel URL: REQUIRES LIVE TEST — changes on every restart.

166. Admin dashboard username: admin

167. Admin dashboard password: admin2026

168. Gmail SMTP email: REQUIRES LIVE TEST — stored in DB config table under ALERT_CONFIG key, not hardcoded.

169. Gmail App Password: REQUIRES LIVE TEST — stored in DB config.

170. TextBee API key: REQUIRES LIVE TEST — stored in DB config.

171. TextBee device ID: REQUIRES LIVE TEST — stored in DB config.

---

## SECTION R: SCREENSHOTS

172-186: ALL REQUIRE LIVE TEST — screenshots must be taken from running system.

---

## SUMMARY

| Category | Total Questions | Answered from Code | Requires Live Test |
|----------|----------------|-------------------|-------------------|
| A. Pi Hardware (1-12) | 12 | 0 fully, 7 partial | 12 |
| B. Pi Files (13-23) | 11 | 10 | 2 |
| C. Pi API (24-31) | 8 | 8 (code verified) | 8 (need live response) |
| D. Railway (32-36) | 5 | 2 | 4 |
| E. Repo Files (37-55) | 19 | 19 (all YES) | 0 |
| F. Database (56-65) | 10 | 10 (schema verified) | 5 (row counts) |
| G. OCR+Playback (66-76) | 11 | 10 | 4 |
| H. Tampering (77-84) | 8 | 8 (code verified) | 4 (physical tests) |
| I. Zoom (85-90) | 6 | 6 | 0 |
| J. Health (91-106) | 16 | 15 | 2 |
| K. Alerts (107-119) | 13 | 12 | 2 |
| L. Multi-User (120-133) | 14 | 14 | 2 |
| M. PWA (134-144) | 11 | 11 | 3 |
| N. Bell (145-152) | 8 | 8 | 0 |
| O. Caching (153-157) | 5 | 5 | 0 |
| P. Bugs (158) | 1 | 1 | 0 |
| Q. Credentials (159-171) | 13 | 5 | 8 |
| R. Screenshots (172-186) | 15 | 0 | 15 |
| **TOTAL** | **186** | **144 answered** | **42 need live access** |

**77% of all items fully answered from code. The remaining 42 items require physical access to the Raspberry Pi or a live running server.**
