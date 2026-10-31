# WAYNE ENTERPRISES SECURE NETWORK TOPOLOGY

- **Core Router**: AT&T BGW Fiber Gateway (192.168.1.254)
- **Mesh AP**: TP-Link Deco XE75 Pro (Smart Connect disabled, 6GHz dedicated, separate SSIDs)
- **Primary SSID**: RAMOSDIAZ (WPA3-Personal)
- **Subnet**: 192.168.1.0/24
- **Tailscale Overlay**: MagicDNS (100.100.100.100), tail178de6.ts.net
- **Firewall Routing**:
  - Ports 80 / 443 -> 192.168.1.135 (ZimaOS NPM)
  - Port 25565 -> 192.168.1.131 (OptiPlex Craftoria)
  - Port 7777 -> 192.168.1.77 (Batcomputer Tactical HUD & API Gateway)
- **DNS Pipeline**: Pi-hole Wildcard *.jmrdserver.duckdns.org -> 192.168.1.135
