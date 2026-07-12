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
CONF_STATUS_INTERVAL = "status_interval"
CONF_ZIGBEE_BASE_TOPIC = "zigbee_base_topic"
CONF_ZWAVE_PREFIX = "zwave_prefix"
CONF_ZWAVE_GATEWAY = "zwave_gateway"
CONF_SERVICE_CHECK_ENABLED = "service_check_enabled"
CONF_SERVICE_CHECK_INTERVAL = "service_check_interval"

DEFAULT_SCAN_RANGES = ["192.168.1.0/24"]
DEFAULT_SCAN_INTERVAL = 3600  # seconds (1h)
DEFAULT_STATUS_INTERVAL = 60   # seconds
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
