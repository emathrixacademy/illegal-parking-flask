# Pi Network Reliability Deploy

SSH into the Pi: `ssh admin@192.168.1.15`

## Step 1: Static IP

```bash
sudo cp /etc/dhcpcd.conf /etc/dhcpcd.conf.bak
echo '
# Static IP for Pi
interface eth0
static ip_address=192.168.1.15/24
static routers=192.168.1.1
static domain_name_servers=192.168.1.1 8.8.8.8' | sudo tee -a /etc/dhcpcd.conf
```

## Step 2: Camera subnet auto-add on boot

```bash
sudo cp camera-subnet.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable camera-subnet
```

## Step 3: Network watchdog

```bash
cp network-watchdog.sh /home/admin/network_watchdog.sh
chmod +x /home/admin/network_watchdog.sh
sudo cp network-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable network-watchdog
sudo systemctl start network-watchdog
```

## Step 4: Ensure core services are enabled

```bash
sudo systemctl enable parking-detect
sudo systemctl enable cloudflared
# hailort must stay DISABLED — parking-detect manages Hailo directly
sudo systemctl disable hailort
```

## Step 5: Reboot and verify

```bash
sudo reboot
# Wait ~60 seconds, then SSH back in
systemctl status camera-subnet
systemctl status network-watchdog
systemctl status parking-detect
systemctl status cloudflared
ping -c1 192.168.8.2    # CAM2
ping -c1 192.168.8.199  # CAM3
ping -c1 192.168.1.3    # CAM1 (VIGI)
```

## What each piece does

| Service | Purpose |
|---------|---------|
| `dhcpcd.conf` static block | Pi always gets 192.168.1.15, no DHCP conflicts |
| `camera-subnet.service` | Adds 192.168.8.100/24 to eth0 on boot (CAM2/CAM3 access) |
| `network-watchdog.service` | Pings 8.8.8.8 every 60s; after 3 failures restarts dhcpcd + cloudflared + re-adds camera subnet |
| `parking-detect` | Already exists — detection + recording |
| `cloudflared` | Already exists — Cloudflare tunnel |

## Rollback

```bash
sudo cp /etc/dhcpcd.conf.bak /etc/dhcpcd.conf
sudo systemctl disable camera-subnet network-watchdog
sudo systemctl stop camera-subnet network-watchdog
sudo reboot
```
