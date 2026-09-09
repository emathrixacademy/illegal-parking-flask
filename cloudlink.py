import subprocess
import re
import threading

TUNNEL_URL_RE = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")


def _drain(process):
    """Keep consuming cloudflared output after the URL has been found.

    stdout is a pipe. If nobody reads it, the OS buffer fills up and cloudflared
    blocks forever on its next write — the tunnel goes dead with no error and no
    exit. Draining in the background keeps it alive.
    """
    try:
        for _ in iter(process.stdout.readline, ''):
            pass
    except Exception:
        pass


def start_cloudflared(port=5000, timeout=60):
    """Start a cloudflared tunnel and return (process, public_url).

    Raises RuntimeError if no URL appears within `timeout` seconds, so a hung
    cloudflared cannot block the caller indefinitely.
    """
    process = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    # Killing the process closes the pipe, which breaks the readline loop below.
    killer = threading.Timer(timeout, process.kill)
    killer.start()

    url = None
    try:
        for line in iter(process.stdout.readline, ''):
            print(line.strip())
            match = TUNNEL_URL_RE.search(line)
            if match:
                url = match.group(0)
                break
    finally:
        killer.cancel()

    if not url:
        process.kill()
        raise RuntimeError(f"Failed to start cloudflared tunnel (no URL after {timeout}s)")

    threading.Thread(target=_drain, args=(process,), daemon=True).start()
    print(f"Cloudflared tunnel running at: {url}")
    return process, url
