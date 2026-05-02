# Homelable for Home Assistant

## WIP ##
/!\ Attention /!\
THis is a WIP project. If you'r interested to test it and give feedback, you are very welcome.
Need feedbacks !

---

Visualize and monitor your homelab network as an interactive topology — inside
Home Assistant.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

This is the Home Assistant integration for
[Homelable](https://github.com/Pouzor/homelable), packaged as a custom
repository for [HACS](https://hacs.xyz/). Need the standalone (Docker / LXC /
Web) version instead? See [Pouzor/homelable](https://github.com/Pouzor/homelable).

---

## Features

- **Interactive topology** as a full Lovelace panel — pan, zoom, drag, group.
- **Network discovery** with nmap, with optional ARP / OS fingerprinting.
- **Live status checks** via ping, HTTP, TCP, or SSH.
- **Rich modeling**: 11 node types (router, switch, server, Proxmox, VM, LXC,
  NAS, IoT, access point, …) and 5 edge types (ethernet, Wi-Fi, IoT, VLAN,
  virtual).

---

## Screenshots

<!-- Add screenshots / GIFs here once you have them.
     Suggested:
       docs/screenshots/canvas.png
       docs/screenshots/scan.png
       docs/screenshots/details.png -->

---

## Installation

### HACS (recommended)

1. **HACS → Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/Pouzor/homelable-hacs` as **Integration**.
3. Install **Homelable**.
4. Restart Home Assistant.
5. **Settings → Devices & Services → Add Integration** → search "Homelable".

### Manual

1. Copy `custom_components/homelable/` into your HA `config/custom_components/`.
2. Restart Home Assistant.
3. Add the integration from the UI as above.

### Requirements

- Home Assistant **2024.1** or newer.
- `nmap` available on the host. (HAOS bundles it; Container / Core users may
  need to install it — see [Scanner privileges](#scanner-privileges).)

---

## Configuration

Setup is fully UI-driven (config flow). You'll be prompted for:

| Field | Default | Description |
|---|---|---|
| Network ranges | `192.168.1.0/24` | Comma-separated CIDR blocks to scan |
| Scan interval | 60 min | How often to look for new devices |
| Status check interval | 60 s | How often to refresh node status |

All values can be changed later from the integration's **Configure** menu.

---

## Usage

After setup, a **Homelable** entry appears in the sidebar. From there:

1. **Run a scan** to discover devices on the configured ranges.
2. **Approve** a discovered device to drop it on the canvas as a node.
3. **Connect** nodes by drawing edges; pick the appropriate edge type.
4. **Save** the canvas (explicit — no autosave).

Scan history, hidden devices, and scan configuration live in the side panel.

---

## Scanner privileges

Full discovery features (ARP, OS detection, SYN scans) need raw socket access.
If raw access is unavailable, the scanner falls back to TCP connect scans —
still useful, but slower and without MAC addresses or OS fingerprints.

| Install type | Notes |
|---|---|
| **HAOS / Supervised** | Companion add-on with full privileges is planned. |
| **Container** | Run the HA container with `CAP_NET_RAW`, or `setcap cap_net_raw+ep $(which nmap)` inside the container. |
| **Core** | `setcap cap_net_raw+ep $(which nmap)` on the host. |

---

## Roadmap

- HA entities per canvas node (`sensor.homelable_<id>`, `binary_sensor.homelable_<id>_online`).
- Device registry: one HA device per canvas node.
- Services: `homelable.scan_now`, `homelable.approve_device`, `homelable.refresh_status`.
- Events: `homelable_node_offline`, `homelable_node_online`, `homelable_device_discovered`.
- HACS default-listing submission once stable with real users.

See the [issue tracker](https://github.com/Pouzor/homelable-hacs/issues) for
the live list.

---

## Contributing

Issues and pull requests are welcome. Please:

- Open an issue first for non-trivial changes so we can align on scope.
- Keep PRs focused and include tests for behavior changes.
- Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`,
  `fix:`, `chore:`, `docs:`, `test:`, `refactor:`).

---

## License

[MIT](LICENSE) © Pouzor
