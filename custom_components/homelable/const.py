"""Constants for the Homelable integration."""

DOMAIN = "homelable"
PLATFORMS: list[str] = []  # Phase 2: ["sensor", "binary_sensor"]

# Storage
STORAGE_KEY_CANVAS = f"{DOMAIN}_canvas"
STORAGE_VERSION_CANVAS = 1

STORAGE_KEY_PENDING = f"{DOMAIN}_pending_devices"
STORAGE_VERSION_PENDING = 1

STORAGE_KEY_RUNS = f"{DOMAIN}_scan_runs"
STORAGE_VERSION_RUNS = 1
MAX_SCAN_RUNS = 50

# Config flow
CONF_SCAN_RANGES = "scan_ranges"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_STATUS_INTERVAL = "status_interval"

DEFAULT_SCAN_RANGES = ["192.168.1.0/24"]
DEFAULT_SCAN_INTERVAL = 3600  # seconds (1h)
DEFAULT_STATUS_INTERVAL = 60   # seconds

# Frontend panel
PANEL_URL = "/homelable_files"
PANEL_TITLE = "Homelable"
PANEL_ICON = "mdi:lan"
PANEL_NAME = "homelable-panel"
