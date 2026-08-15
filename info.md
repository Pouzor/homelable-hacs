# Homelable

Visualize and monitor your homelab network as an interactive topology, inside Home Assistant.

## Features

- Interactive network topology canvas as a Lovelace panel
- nmap-based local network scanning + service fingerprinting
- Live status monitoring (ping/HTTP/SSH/TCP)
- 11 node types covering routers, switches, servers, Proxmox + VM/LXC, NAS, IoT, APs
- Zigbee import from ZHA or Zigbee2MQTT, plus Z-Wave JS through HA's MQTT integration
- Proxmox VE import (hosts / VMs / LXC) via a read-only API token, with optional auto-sync
- Canvas persisted via HA Storage — no external database
- Uses HA native auth — no extra login

## Setup

Add via Settings → Devices & Services → Add Integration → "Homelable".

You'll configure network ranges to scan and check intervals.

## Links

- [Documentation](https://github.com/Pouzor/homelable-hacs)
- [Issues](https://github.com/Pouzor/homelable-hacs/issues)
- [Standalone version](https://github.com/Pouzor/homelable)
