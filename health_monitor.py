"""
System Health Monitoring Module (Feature 16)
Runs on the Raspberry Pi edge device.
Monitors CPU temperature, RAM, disk usage, and camera connectivity.
"""
import subprocess
import time
import logging
import cv2

logger = logging.getLogger("HealthMonitor")

# Try to import psutil; gracefully degrade if not available
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    logger.warning("psutil not installed. System metrics will be limited.")


class HealthMonitor:
    def __init__(self):
        self.status = {}
        self.camera_status = {}

    def get_cpu_temp(self):
        """Get Raspberry Pi CPU temperature."""
        try:
            result = subprocess.run(
                ['vcgencmd', 'measure_temp'],
                capture_output=True, text=True, timeout=5
            )
            temp_str = result.stdout.strip()  # "temp=45.0'C"
            return float(temp_str.split('=')[1].split("'")[0])
        except Exception:
            return -1

    def get_system_metrics(self):
        """Get current system health metrics."""
        if not HAS_PSUTIL:
            return {
                "cpu_temp": self.get_cpu_temp(),
                "cpu_percent": -1,
                "memory_total_mb": -1,
                "memory_used_mb": -1,
                "memory_percent": -1,
                "disk_total_gb": -1,
                "disk_used_gb": -1,
                "disk_percent": -1,
                "recording_disk_gb": None,
                "uptime_seconds": -1,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
            }

        disk = psutil.disk_usage('/')
        recording_disk = None
        try:
            recording_disk = psutil.disk_usage('/mnt/recording')
        except Exception:
            pass

        return {
            "cpu_temp": self.get_cpu_temp(),
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory_total_mb": round(psutil.virtual_memory().total / 1048576),
            "memory_used_mb": round(psutil.virtual_memory().used / 1048576),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_total_gb": round(disk.total / 1073741824, 1),
            "disk_used_gb": round(disk.used / 1073741824, 1),
            "disk_percent": round(disk.percent, 1),
            "recording_disk_gb": round(recording_disk.free / 1073741824, 1) if recording_disk else None,
            "uptime_seconds": int(time.time() - psutil.boot_time()),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
        }

    def check_camera(self, camera_id, rtsp_url):
        """Check if a camera RTSP stream is accessible."""
        try:
            cap = cv2.VideoCapture(rtsp_url)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
            ret, _ = cap.read()
            cap.release()
            return {"online": ret, "status": "online" if ret else "offline"}
        except Exception:
            return {"online": False, "status": "error"}

    def get_full_health(self, cameras):
        """Get complete health report."""
        metrics = self.get_system_metrics()
        camera_health = {}
        for cam_id, rtsp_url in cameras.items():
            camera_health[cam_id] = self.check_camera(cam_id, rtsp_url)

        # Determine alerts
        alerts = []
        if metrics["cpu_temp"] > 80:
            alerts.append({"level": "critical", "msg": f"CPU temperature high: {metrics['cpu_temp']}C"})
        if HAS_PSUTIL:
            if metrics["memory_percent"] > 90:
                alerts.append({"level": "warning", "msg": f"Memory usage high: {metrics['memory_percent']}%"})
            if metrics["disk_percent"] > 90:
                alerts.append({"level": "warning", "msg": f"Disk usage high: {metrics['disk_percent']}%"})
        for cam_id, status in camera_health.items():
            if not status["online"]:
                alerts.append({"level": "critical", "msg": f"{cam_id} is offline"})

        return {
            "system": metrics,
            "cameras": camera_health,
            "alerts": alerts,
            "overall_status": "critical" if any(a["level"] == "critical" for a in alerts)
                              else "warning" if alerts else "healthy"
        }
