"""Find a camera again after it has moved to a different IP address.

CAM1 (the VIGI) takes a DHCP lease, so a power cut can hand it a different address —
it has already moved from .14 to .3 once. When that happens the RTSP URL is wrong in
three places at once (config.py, Railway, and the running stream) and retrying the
old address recovers nothing, no matter how long it is left alone. Until now the only
cure was an engineer driving to the site to re-scan the subnet by hand.

This scans the camera's own /24 for a host answering on its RTSP port, proves the
candidate by actually pulling a frame from it with the same credentials and path, and
then publishes the new URL back to Railway so the repair survives the next restart
and shows up on the dashboard instead of living only in this process's memory.
"""

import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("CameraRecovery")

PORT_PROBE_TIMEOUT = 0.4   # seconds per host; the whole /24 is swept in parallel
SCAN_WORKERS = 48
OPEN_TIMEOUT_MS = 5000


def host_and_port(url, default_port=554):
    """Pull the bare host and port out of an RTSP URL, ignoring any credentials."""
    hostport = urlsplit(url).netloc.rpartition("@")[2]
    if ":" in hostport:
        host, _, port = hostport.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return host, default_port
    return hostport, default_port


def replace_host(url, new_host):
    """Rebuild an RTSP URL against new_host, preserving credentials, port and path.

    Deliberately done by string surgery on the netloc rather than through urlsplit's
    username/password properties: CAM1's password is URL-encoded (``%40`` for ``@``)
    and round-tripping it through those properties risks mangling the escape, which
    turns a working password into a 401 that looks exactly like a dead camera.
    """
    parts = urlsplit(url)
    userinfo, sep, hostport = parts.netloc.rpartition("@")
    port = ""
    if ":" in hostport:
        port = ":" + hostport.rpartition(":")[2]
    return urlunsplit((parts.scheme, f"{userinfo}{sep}{new_host}{port}",
                       parts.path, parts.query, parts.fragment))


def _port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=PORT_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def scan_subnet(reference_ip, port, skip=()):
    """Return every host in reference_ip's /24 that accepts a connection on port."""
    octets = reference_ip.split(".")
    if len(octets) != 4 or not all(o.isdigit() for o in octets):
        logger.warning("Cannot scan around %r — not a dotted-quad address", reference_ip)
        return []
    prefix = ".".join(octets[:3])
    skip = set(skip)
    candidates = [f"{prefix}.{i}" for i in range(1, 255) if f"{prefix}.{i}" not in skip]
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        results = pool.map(lambda h: _port_open(h, port), candidates)
        return [host for host, is_open in zip(candidates, results) if is_open]


class CameraRecovery:
    """Watches streams that have gone quiet and hunts for their new address.

    offline_grace exists so a brief RTSP hiccup or a camera rebooting on its own does
    not trigger a subnet sweep; retry_interval keeps a genuinely dead camera from
    causing a scan every single minute for the rest of the week.
    """

    def __init__(self, streams, cv2_module, on_recovered=None,
                 offline_grace=180, interval=60, retry_interval=600):
        self.streams = streams              # {"Camera_1": Stream, ...}; values may be None
        self.cv2 = cv2_module
        self.on_recovered = on_recovered    # called as (camera_name, new_url)
        self.offline_grace = offline_grace
        self.interval = interval
        self.retry_interval = retry_interval
        self._last_attempt = {}

    # -------------------------------------------------------------- internals

    def _frame_arrives(self, url):
        cap = self.cv2.VideoCapture(url, self.cv2.CAP_FFMPEG)
        try:
            for prop in ("CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC"):
                try:
                    cap.set(getattr(self.cv2, prop), OPEN_TIMEOUT_MS)
                except Exception:
                    pass
            if not cap.isOpened():
                return False
            ok, frame = cap.read()
            return bool(ok) and frame is not None
        except Exception:
            return False
        finally:
            cap.release()

    def _hosts_in_use(self, exclude):
        """Addresses other cameras are currently streaming from.

        Skipping these stops a sweep for one camera from "finding" a different camera
        that is working perfectly well and stealing its address, which would take out
        two cameras instead of fixing one.
        """
        in_use = set()
        for name, stream in self.streams.items():
            if name == exclude or stream is None:
                continue
            try:
                in_use.add(host_and_port(stream.current_url())[0])
            except Exception:
                pass
        return in_use

    def _rediscover(self, name, stream):
        url = stream.current_url()
        old_host, port = host_and_port(url)
        logger.warning("%s offline for %ds — sweeping %s.0/24 for it",
                       name, int(stream.offline_seconds()), old_host.rsplit(".", 1)[0])

        skip = self._hosts_in_use(exclude=name) | {old_host}
        candidates = scan_subnet(old_host, port, skip=skip)
        if not candidates:
            logger.warning("%s: nothing else on that subnet answers on port %d", name, port)
            return False

        for candidate in candidates:
            new_url = replace_host(url, candidate)
            if self._frame_arrives(new_url):
                logger.info("%s found at %s — adopting its new address", name, candidate)
                stream.set_url(new_url)
                if self.on_recovered:
                    try:
                        self.on_recovered(name, new_url)
                    except Exception as e:
                        logger.warning("%s: could not publish the new URL: %s", name, e)
                return True

        logger.warning("%s: %d host(s) answered on port %d but none served a frame",
                       name, len(candidates), port)
        return False

    def _one_pass(self):
        now = time.time()
        for name, stream in self.streams.items():
            if stream is None or stream.is_online():
                continue
            if stream.offline_seconds() < self.offline_grace:
                continue
            if now - self._last_attempt.get(name, 0) < self.retry_interval:
                continue
            self._last_attempt[name] = now
            self._rediscover(name, stream)

    # ------------------------------------------------------------------ public

    def run_forever(self):
        while True:
            time.sleep(self.interval)
            try:
                self._one_pass()
            except Exception as e:
                logger.warning("Recovery pass failed: %s", e)

    def start(self):
        threading.Thread(target=self.run_forever, daemon=True).start()
        logger.info("Camera IP recovery running (grace=%ds, rescan every %ds)",
                    self.offline_grace, self.retry_interval)
