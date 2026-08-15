# Homelable for Home Assistant — Features

Here's what Homelable can do inside Home Assistant. One line on what each feature is, then how to switch it on and use it.

> **Home Assistant integration.** Everything runs inside HA. Configuration is UI-driven (config flow + the integration's **Configure** menu), scanning is pure Python, ZHA is read straight from the integration, and the MQTT imports reuse the broker HA already talks to.

---

## Table of Contents

1. [Zones](#1-zones)
2. [Groups & Nesting](#2-groups--nesting)
3. [Text Annotations](#3-text-annotations)
4. [Multiple Canvases](#4-multiple-canvases)
5. [Customize Style](#5-customize-style)
6. [Floor Plan](#6-floor-plan)
7. [Network Scanner (device discovery)](#7-network-scanner-device-discovery)
8. [Zigbee Import](#8-zigbee-import)
9. [Z-Wave Import](#9-z-wave-import)
10. [Proxmox VE Import](#10-proxmox-ve-import)
11. [Device Inventory](#11-device-inventory)
12. [Live Status Monitoring](#12-live-status-monitoring)
13. [Export (PNG / SVG / YAML / Markdown)](#13-export)
14. [Settings & Shortcuts](#14-settings--shortcuts)

---

## 1. Zones

**What:** Labeled boxes to group devices by area: "Living room", "Rack 1", "DMZ", whatever makes sense to you.

**Use:**
- Sidebar → **Add Zone**. Give it a title and a color, then drag it around and resize it.
- Drop nodes onto a zone and Homelable asks if you want to add them to it.
- Zones sit behind your nodes and move on their own. They're just there to keep things tidy.

---

## 2. Groups & Nesting

**What:** Some devices hold others, like a **Proxmox** host with its **VMs** and **LXCs** inside. Those show up as an expandable container.

**Use:**
- Drag a `vm` or `lxc` onto a `proxmox` node, confirm **Add to container**, and it becomes a child.
- Click the container header to fold it open or shut; it resizes itself around what's inside.
- The Zigbee and Z-Wave imports build the same kind of hierarchy for you (coordinator → routers → end devices).

---

## 3. Text Annotations

**What:** Loose text labels for notes, section titles, or anything you want to call out on the canvas.

**Use:** Sidebar → **Add Text**, type, drop it where you want, style it.

---

## 4. Multiple Canvases

**What:** More than one diagram in a single install, say "Network", "Home automation", "Rack layout", each with its own nodes, links, floor plan, and style.

**Use:**
- The **canvas switcher** is at the top of the sidebar. Click to jump between canvases, or **New Canvas** to start a fresh one.
- Hover a canvas to **Edit** it (name, icon, floor plan) or **Delete** it. You can't delete the last one.
- Each canvas saves on its own, so hit **Save Canvas** after you change something.

---

## 5. Customize Style

**What:** Repaint the whole thing with a preset theme, or roll your own node and edge colors.

**Use:**
- Toolbar → **Style**. Pick a preset: **Default**, **Dark**, **Light**, **Neon**, or **Matrix**.
- Or pick **Custom** and hit its **Edit** button to set border/background colors per node type and link colors per edge type.
- Theme and custom colors are saved **per canvas** on your next **Save Canvas**.

---

## 6. Floor Plan

**What:** Put a background image (a house plan, an office layout, a rack diagram) behind a canvas and lay your devices out on top of it.

**Use:**
- Open the **canvas switcher** → **Edit** the active canvas (or double-click the floor plan already on the canvas).
- In the **Floor Plan** section, upload an image and set its size and lock state.
- The image is stored by the integration and loaded by URL, never baked into the canvas, so your canvases stay light.

---

## 7. Network Scanner (device discovery)

**What:** Discover hosts on your network and turn them into nodes — pure Python, no nmap, no external binaries.

**Use:**
1. Sidebar → **Scan Network**. Scan History opens and keeps refreshing until it's done.
2. Set the CIDR ranges you want during setup (config flow) or later from the integration's **Configure** menu.
3. Whatever it finds shows up in the **Device Inventory** (below) to approve, hide, or ignore.

**How it discovers:** ping sweep + ARP cache + a pure-Python TCP connect scan for service detection + mDNS / zeroconf. It works on every install without privileges; raw socket access (ICMP ping, richer ARP) yields more reliable discovery and MAC addresses. See [Scanner privileges](./README.md#scanner-privileges).

---

## 8. Zigbee Import

**What:** Pull your Zigbee topology in and drop every device on the canvas as a typed node. Works with either gateway:

- **ZHA** — read straight from Home Assistant's own ZHA integration. No MQTT broker, no re-pairing, and it returns instantly.
- **Zigbee2MQTT** — a network-map request over the MQTT broker HA already talks to.

**Use:**
1. Sidebar → **Zigbee Import**.
2. Nothing to configure for ZHA. For Zigbee2MQTT, set the base topic (default `zigbee2mqtt`) if you changed it.
3. If you run both gateways, pick one in the dialog; otherwise it uses whichever you have (ZHA first).
4. **Start Zigbee scan** → results land in **Pending** → approve them onto the canvas.

Nodes come in as `zigbee_coordinator` / `zigbee_router` / `zigbee_enddevice`. The hierarchy (coordinator → routers → end devices) and **LQI** are filled in automatically — ZHA reads them from the radio's neighbour tables, Z2M from the network map.

---

## 9. Z-Wave Import

**What:** Same idea for **Z-Wave JS UI**, over the same MQTT broker HA already uses.

**Use:**
1. Sidebar → **Z-Wave Import**.
2. Set the MQTT prefix (default `zwave`) and the gateway name (default `zwavejs2mqtt`) if you changed them.
3. **Fetch Devices** → send them to **Pending** or straight to the **Canvas** → pick devices → **Add N to Canvas**.

Nodes: `zwave_coordinator` / `zwave_router` / `zwave_enddevice`. The hierarchy comes from each node's neighbor list (Z-Wave has no LQI).

---

## 10. Proxmox VE Import

**What:** Pull your **Proxmox VE** inventory (hosts, VMs, LXC) in over the Proxmox REST API — typed, named nodes with run state and hardware specs. Optional scheduled **auto-sync**; guest IPs already found by a scan are merged (by MAC), not duplicated.

**Use:**
1. Create a read-only API token in Proxmox (Datacenter → Permissions → API Tokens, role `PVEAuditor` at path `/`).
2. Add the token in the integration's **Configure** menu (`user@realm!tokenid` + secret).
3. Sidebar → **Proxmox Import** → **Test Connection** → send to **Pending** or the **Canvas** → import → pick devices → **Add N to Canvas**.

Nodes: `proxmox` (host) / `vm` / `lxc`, linked host→guest by a `virtual` edge; cluster hosts chain together. vCPU / RAM / disk come in as node properties. Enable **auto-sync** from the integration options to re-import on a schedule.

---

## 11. Device Inventory

**What:** The holding pen for everything found by a scan or import that isn't on the canvas yet, plus a separate **Hidden Devices** list.

**Use:**
- Sidebar → **Device Inventory**. Each entry shows IP, MAC, hostname, and any OS and services detected.
- Per device: **Approve** to drop a typed node on the canvas, **Hide** to stash it (you can get it back), or **Ignore** to dismiss it.
- **Hidden Devices** is the sidebar entry where you review and restore anything you've hidden.

---

## 12. Live Status Monitoring

**What:** Keeps checking each node and shows its status (🟢 online / 🔴 offline / ⚫ unknown) right on the canvas.

**Use:**
- Pick a **check method** per node when you add or edit it:

  | Method | Checks |
  |--------|--------|
  | `ping` | ICMP reachability |
  | `http` | GET, OK if status < 500 |
  | `https` | GET with TLS verify |
  | `tcp` | TCP connect to `host:port` |
  | `ssh` | TCP connect to port 22 |
  | `prometheus` | GET `/metrics` |
  | `health` | GET `/health` |

- Checks run on a timer (the **status check interval**, 60s by default — set it in the integration options) and stream to the UI, no refresh. The sidebar footer keeps a running online/offline tally.

---

## 13. Export

**What:** Get your canvas out as a picture or as structured data.

**Use (toolbar):**
- **PNG**, a snapshot of the canvas, quality of your choice.
- **SVG**, vector export, keeps fonts, icons, and colors crisp. Same dialog as PNG.
- **Export (YAML)**, the whole canvas (nodes + links) as YAML you can re-import.
- **Markdown**, copies your device inventory as a Markdown table, handy for docs or a README.

---

## 14. Settings & Shortcuts

**What:** App config and keyboard shortcuts.

**Use:**
- Sidebar → **Settings** for app-level config.
- **Search** to find nodes fast.
- Open the **Shortcuts** modal for the full key list (Save `Ctrl/Cmd+S`, undo/redo, and the rest).

---

*Installing (HACS or manual) and configuration are covered in the [README](./README.md). Need the standalone Docker / LXC / Web version? See [Pouzor/homelable](https://github.com/Pouzor/homelable).*
