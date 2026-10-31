# FLEET NODES & TACTICAL INFRASTRUCTURE

- **Mac Mini M1 (Hermes / Batcomputer Host)**:
  - Local IP: 192.168.1.77
  - Tailscale IP: 100.77.249.27 (mac-mini-m1.tail178de6.ts.net)
  - Specs: Apple M1, 8GB Unified RAM, macOS 15.6.1
  - Services: Batcomputer HUD (7777), VoiceStudio (3900), Ollama (11434)

- **MacBook Pro M2 (Tactical Field Client / Gaming Rig)**:
  - Tailscale IP: 100.78.121.43
  - Specs: Apple M2, 8GB Soldered RAM
  - Role: Primary mobile command station, Prism, Craftoria

- **Gaming PC (Omarchy Arch Linux)**:
  - Local IP: 192.168.1.202
  - Tailscale IP: 100.111.120.9 / 100.98.14.11
  - Specs: AMD Ryzen 7 5800X, NVIDIA GeForce GTX 1080, 16GB DDR4-2400, B550M Steel Legend, Cooler Master Q300L
  - Storage: KIOXIA 512GB NVMe, 1.8TB HDD (/mnt/games)
  - Bootloader: Limine + Unified Kernel Image (UKI)

- **OptiPlex 9010 (Remote Compute & Crafty MC Server)**:
  - Local IP: 192.168.1.131
  - Services: Crafty Controller (Port 8443, Java 25), Nextcloud (8081), MC Craftoria (25565), JM Hardcore (25580)

- **ZimaOS / jmserver (Storage & Core Ingress)**:
  - Local IP: 192.168.1.135
  - Tailscale IP: 100.84.108.41
  - Services: Nginx Proxy Manager (80/81/443), Pi-hole (DNS 53, Web 8888), RustDesk Relay (21115-21119)

- **HP ProDesk 600 G3 (Proxmox VE Cluster)**:
  - Tailscale IP: 100.103.137.34
  - Services: Proxmox VE, Home Assistant OS, ZimaOS VM
