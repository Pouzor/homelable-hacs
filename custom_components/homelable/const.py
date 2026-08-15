"""Constants for the Homelable integration."""

DOMAIN = "homelable"
PLATFORMS: list[str] = []  # Phase 2: ["sensor", "binary_sensor"]

# Storage
STORAGE_KEY_CANVAS = f"{DOMAIN}_canvas"
STORAGE_VERSION_CANVAS = 1

STORAGE_KEY_DESIGNS = f"{DOMAIN}_designs"
STORAGE_VERSION_DESIGNS = 1

# Default design seeded on first run / legacy migration. Existing single-canvas
# data is migrated into a design with this name so HA users lose nothing.
DEFAULT_DESIGN_NAME = "Network Topology"
DEFAULT_DESIGN_ICON = "network"
DEFAULT_DESIGN_TYPE = "network"

STORAGE_KEY_PENDING = f"{DOMAIN}_pending_devices"
STORAGE_VERSION_PENDING = 1

STORAGE_KEY_RUNS = f"{DOMAIN}_scan_runs"
STORAGE_VERSION_RUNS = 1
MAX_SCAN_RUNS = 50

# Config flow
CONF_SCAN_RANGES = "scan_ranges"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SCAN_AUTO_ENABLED = "scan_auto_enabled"
CONF_STATUS_INTERVAL = "status_interval"
CONF_ZIGBEE_BASE_TOPIC = "zigbee_base_topic"
CONF_ZWAVE_PREFIX = "zwave_prefix"
CONF_ZWAVE_GATEWAY = "zwave_gateway"
CONF_SERVICE_CHECK_ENABLED = "service_check_enabled"
CONF_SERVICE_CHECK_INTERVAL = "service_check_interval"

# Proxmox VE import. Token is a real credential, stored in the config entry
# options like any HA integration credential; it is never returned by any WS
# command (only `token_configured` is surfaced). Non-secret connection + auto-
# sync config lives alongside it in options.
CONF_PROXMOX_HOST = "proxmox_host"
CONF_PROXMOX_PORT = "proxmox_port"
CONF_PROXMOX_TOKEN_ID = "proxmox_token_id"
CONF_PROXMOX_TOKEN_SECRET = "proxmox_token_secret"
CONF_PROXMOX_VERIFY_TLS = "proxmox_verify_tls"
CONF_PROXMOX_SYNC_ENABLED = "proxmox_sync_enabled"
CONF_PROXMOX_SYNC_INTERVAL = "proxmox_sync_interval"

DEFAULT_PROXMOX_PORT = 8006
DEFAULT_PROXMOX_VERIFY_TLS = True
DEFAULT_PROXMOX_SYNC_ENABLED = False
DEFAULT_PROXMOX_SYNC_INTERVAL = 3600  # seconds (1h)
MIN_PROXMOX_SYNC_INTERVAL = 300  # seconds (5 min)

# Discovery-source tags for the two Proxmox link shapes. Host→guest links render
# as 'virtual' edges; host↔host cluster links render as 'cluster' edges.
PROXMOX_SOURCE = "proxmox"
PROXMOX_CLUSTER_SOURCE = "proxmox_cluster"

DEFAULT_SCAN_RANGES = ["192.168.1.0/24"]
# Periodic scans are opt-in. A sweep is by far the heaviest thing this
# integration does, so it never runs on a timer unless the user says so; the
# interval below only applies once CONF_SCAN_AUTO_ENABLED is on.
DEFAULT_SCAN_AUTO_ENABLED = False
DEFAULT_SCAN_INTERVAL = 3600  # seconds (1h)
MIN_SCAN_INTERVAL = 300  # seconds (5 min)
DEFAULT_STATUS_INTERVAL = 60   # seconds

# Node status checks fan out one subprocess per node (ping). Unbounded, a large
# canvas forks dozens of processes at once every poll, which starves low-memory
# hosts (RPi / HA Green) and trips the Supervisor watchdog. Cap the fan-out.
STATUS_CHECK_CONCURRENCY = 10

# `last_seen` is refreshed in memory on every poll, but the canvas Store is only
# rewritten when the stored value is older than this. Without it a canvas with
# one online node rewrites the whole Store every DEFAULT_STATUS_INTERVAL,
# hammering SD cards for a timestamp nobody reads at that resolution.
LAST_SEEN_PERSIST_INTERVAL = 300  # seconds (5 min)

# Delay (seconds) for the debounced canvas write that carries `last_seen`.
# Store flushes pending delayed saves on HA shutdown, so nothing is lost.
LAST_SEEN_SAVE_DELAY = 30
DEFAULT_ZIGBEE_BASE_TOPIC = "zigbee2mqtt"
DEFAULT_ZWAVE_PREFIX = "zwave"
DEFAULT_ZWAVE_GATEWAY = "zwavejs2mqtt"
# Per-service status checks are independent of node checks and off by default.
DEFAULT_SERVICE_CHECK_ENABLED = False
DEFAULT_SERVICE_CHECK_INTERVAL = 300  # seconds (5 min)
MIN_SERVICE_CHECK_INTERVAL = 30   # seconds

# Zigbee networkmap timeouts (seconds). Large meshes (>50 devices) routinely
# take 2-4 minutes; coordinator polls every router for routing tables.
ZIGBEE_NETWORKMAP_TIMEOUT = 300.0

# Z-Wave JS UI getNodes timeout (seconds). Large meshes are slow; the gateway
# polls every node before answering.
ZWAVE_NODES_TIMEOUT = 300.0

# Dispatcher signal for live scan events (device_discovered / device_enriched
# / scan_phase / scan_finished / scan_cancelled). Subscribers receive a single
# dict payload; see websocket.ws_scan_subscribe.
SCAN_SIGNAL = f"{DOMAIN}_scan_event"

# Dispatcher signal for live per-service status results. Subscribers receive a
# dict payload {node_id, services: [{port, protocol, status}], checked_at}; see
# websocket.ws_service_status_subscribe.
SERVICE_STATUS_SIGNAL = f"{DOMAIN}_service_status"

# Frontend panel
PANEL_URL = "/homelable_files"
PANEL_TITLE = "Homelable"
PANEL_ICON = "mdi:lan"
PANEL_NAME = "homelable-panel"

# Media (floor-plan images and future raw image uploads) live on disk under the
# HA config dir, served via a public static path. Filenames are server-generated
# UUIDs (unguessable), so GET is public like the panel bundle; upload/delete
# require an authenticated request. Guards against re-registering the view.
MEDIA_URL = "/homelable_media"
MEDIA_DIR = f"{DOMAIN}/media"  # relative to hass.config.path(...)
MEDIA_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
MEDIA_REGISTERED_KEY = f"{DOMAIN}_media_registered"
