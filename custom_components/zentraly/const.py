"""Constants for Zentraly integration."""
from homeassistant.const import Platform

DOMAIN = "zentraly"
SERVICE_REFRESH_DEVICE = "refresh_device"
SERVICE_APPLY_THERMOSTAT_ADVANCED_SETTINGS = "apply_thermostat_advanced_settings"
SERVICE_APPLY_BOILER_SETTINGS = "apply_boiler_settings"
PLATFORMS = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.LOCK,
    Platform.BUTTON,
]

# API
API_BASE_URL = "https://ztprdrestservicesv2.azurewebsites.net"
API_LOGIN_ENDPOINT = "/Login"
API_APP_ENDPOINT = "/App"
API_IOT_COMMAND_ENDPOINT = "/app/Action"
API_FIREBASE_KEY = "f06d3a055c7066de31d6d1ae583d7bd18d99840bc74d14aaed63860054004f15"
API_FIREBASE_IV = "eeed3a055c7066de31d6d1ae27017bd1"
ZENTRALY_APP_VERSION = "7.2.0"
DC_OPER_RUN_IOT = 28

# Auth prefixes
AUTH_PREFIX_LOGIN = "ztv2Auth"
AUTH_PREFIX_TOKEN = "ztv2Token"

# Device types
DEVICE_TYPE_THERMOSTAT = 2
DEVICE_TYPE_ZTTIN01_THERMOSTAT = 16
DEVICE_TYPE_BOILER = 17
THERMOSTAT_DEVICE_TYPES = {
    DEVICE_TYPE_THERMOSTAT,
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
}
BOILER_DEVICE_TYPES = {
    DEVICE_TYPE_BOILER,
}

# Temperature conversion (API uses centidegrees)
TEMP_SCALE = 100

# Commands
CMD_GET_CONFIG = "getConfig"
CMD_SET_CONFIG = "setConfig"
CMD_READ_ATTR = "readAttr"
CMD_WRITE_ATTR = "writeAttr"
CMD_GET_OFFSET_TEMP = "getOffsetTemp"

# ZTTIN01 protocol
ZTTIN01_CLUSTER_THERMOSTAT = 65513
ZTTIN01_ATTR_CURRENT_TEMPERATURE = 0
ZTTIN01_ATTR_HUMIDITY = 1
ZTTIN01_ATTR_TARGET_TEMPERATURE = 18
ZTTIN01_ATTR_MODE = 28
ZTTIN01_ATTR_LOCK = 90
ZTTIN01_ATTR_SCHEDULE = 4
ZTTIN01_ATTR_TYPE_INT = 41
ZTTIN01_ATTR_TYPE_HEX = 72
ZTTIN01_ATTR_TYPE_LONG = 43
ZTTIN01_READ_ATTRS = [
    {"id": ZTTIN01_ATTR_CURRENT_TEMPERATURE, "type": ZTTIN01_ATTR_TYPE_INT},
    {"id": ZTTIN01_ATTR_HUMIDITY, "type": ZTTIN01_ATTR_TYPE_INT},
    {"id": ZTTIN01_ATTR_TARGET_TEMPERATURE, "type": ZTTIN01_ATTR_TYPE_INT},
    {"id": ZTTIN01_ATTR_MODE, "type": ZTTIN01_ATTR_TYPE_INT},
    {"id": ZTTIN01_ATTR_SCHEDULE, "type": ZTTIN01_ATTR_TYPE_HEX},
    {"id": 17, "type": ZTTIN01_ATTR_TYPE_INT},
    {"id": ZTTIN01_ATTR_LOCK, "type": ZTTIN01_ATTR_TYPE_INT},
    {"id": 16, "type": ZTTIN01_ATTR_TYPE_INT},
    {"id": 100, "type": ZTTIN01_ATTR_TYPE_INT},
    {"id": 101, "type": ZTTIN01_ATTR_TYPE_INT},
    {"id": 102, "type": ZTTIN01_ATTR_TYPE_INT},
]
ZTTIN01_RAW_ATTR_READS = {
    ZTTIN01_CLUSTER_THERMOSTAT: [
        {"id": 16, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 17, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 100, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 101, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 102, "type": ZTTIN01_ATTR_TYPE_INT},
    ],
    65006: [
        {"id": 0, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 13000, "type": ZTTIN01_ATTR_TYPE_LONG},
    ],
}
BOILER_RAW_ATTR_READS = {
    65006: [
        {"id": 0, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 2, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 10, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 11, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 13000, "type": ZTTIN01_ATTR_TYPE_LONG},
    ],
    65535: [
        {"id": 1000, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 1, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 56, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 1056, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 1001, "type": ZTTIN01_ATTR_TYPE_INT},
        {"id": 10001, "type": ZTTIN01_ATTR_TYPE_INT},
    ],
}
ZTTIN01_DEFAULT_ENDPOINT = 1
ZTTIN01_COMMAND_TIMEOUT = 30000
ZTTIN01_DEFAULT_HEAT_TEMPERATURE = 21.0
ZTTIN01_OFF_TEMPERATURE = 5.0
ZTTIN01_AWAY_TEMPERATURE = 17.0

ZTTIN01_MODE_OFF = 0
ZTTIN01_MODE_MANUAL = 1
ZTTIN01_MODE_AUTO = 2
ZTTIN01_MODE_AWAY = 3

ZENTRALY_PRESET_NONE = "none"
ZENTRALY_PRESET_AWAY = "away"

# Config IDs for getConfig
CONFIG_IDS = [
    "targetTemp",
    "temperature",
    "thermostatMode",
    "humidity",
    "ssid",
    "rssi",
    "output",
    "lock",
    "service"
]

# Legacy thermostat modes (kept for device_type=2 compatibility)
HVAC_MODE_MAP = {
    1: "heat",
    2: "cool",  # Assuming
    3: "auto",  # Assuming
    4: "off"
}

HVAC_MODE_REVERSE = {v: k for k, v in HVAC_MODE_MAP.items()}

# Update interval
SCAN_INTERVAL_SECONDS = 60

# Conf keys
CONF_USER_ID = "user_id"
CONF_TOKEN = "token"
CONF_DEVICE_GUID = "device_guid"
