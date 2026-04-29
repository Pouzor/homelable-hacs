# Homelable for Home Assistant

Visualize and monitor your homelab network as an interactive topology, **inside Home Assistant**.

This is the official Home Assistant integration for [Homelable](https://github.com/Pouzor/homelable), distributed via [HACS](https://hacs.xyz/).

> Standalone (Docker/LXC) version: see [Pouzor/homelable](https://github.com/Pouzor/homelable).

---

## Features

- 🗺️ Interactive network topology canvas as a Lovelace panel
- 🔍 Scan local networks (nmap) and discover devices
- 📡 Live status monitoring (ping/HTTP/SSH/TCP)
- 🧩 11 node types (router, switch, server, Proxmox, VM, LXC, NAS, IoT, AP, …)
- 🔌 5 edge types (ethernet, wifi, IoT, VLAN, virtual)
- 💾 Canvas persisted via HA Storage (no external DB)
- 🔐 No extra auth — uses your HA login

**Phase 2 (planned):** HA entities (sensor/binary_sensor) per node + services + automations.

---

## Installation

### Via HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/Pouzor/homelable-hacs` as **Integration**
3. Install **Homelable**
4. Restart Home Assistant
5. Settings → Devices & Services → Add Integration → search "Homelable"

### Manual

1. Copy `custom_components/homelable/` into your HA `config/custom_components/`
2. Restart Home Assistant
3. Add the integration via UI

---

## Configuration

Setup is via the HA UI (config flow). You'll be asked:
- **Network ranges** to scan (CIDR, e.g. `192.168.1.0/24`)
- **Scan interval** (default: 60 minutes)
- **Status check interval** (default: 60 seconds)

---

## Scanner Privileges

Some scan features (ARP discovery, OS detection, SYN scans) require raw network access:
- **HAOS / Supervised**: install the Homelable add-on (planned) for full features.
- **Container / Core**: full features require `CAP_NET_RAW` on the HA container or `setcap cap_net_raw+ep $(which nmap)`.
- **Without raw access**: integration falls back to TCP connect scans (slower, no MAC addresses, no OS fingerprint).

---

## License

MIT © Remy Jardinet
