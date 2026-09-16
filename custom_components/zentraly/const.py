"""Constants for Zentraly integration."""
from homeassistant.const import Platform

DOMAIN = "zentraly"
PLATFORMS = [Platform.CLIMATE]

# API
API_BASE_URL = "https://ztprdrestservicesv2.azurewebsites.net"
API_LOGIN_ENDPOINT = "/Login"
API_APP_ENDPOINT = "/App"
API_IOT_COMMAND_ENDPOINT = "/app/Action"
API_FIREBASE_KEY = "f06d3a055c7066de31d6d1ae583d7bd18d99840bc74d14aaed63860054004f15"
API_FIREBASE_IV = "eeed3a055c7066de31d6d1ae27017bd1"
ZENTRALY_APP_VERSION = "7.1.6"

# API operations
DC_OPER_RUN_IOT = 28

# Auth prefixes
AUTH_PREFIX_LOGIN = "ztv2Auth"
AUTH_PREFIX_TOKEN = "ztv2Token"

# Device types
DEVICE_TYPE_THERMOSTAT = 2

# Temperature conversion (API uses centidegrees)
TEMP_SCALE = 100

# Commands
CMD_GET_CONFIG = "getConfig"
CMD_SET_CONFIG = "setConfig"
CMD_GET_OFFSET_TEMP = "getOffsetTemp"

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

# Wi-Fi wire modes, verified against Zentraly 7.2.0's setConfig generator.
# Readback can report 4 during heating; only 0 represents OFF.
THERMOSTAT_MODE_OFF = 0
THERMOSTAT_MODE_MANUAL = 2

# Update interval
SCAN_INTERVAL_SECONDS = 60

# Conf keys
CONF_USER_ID = "user_id"
CONF_TOKEN = "token"
CONF_FIREBASE_TOKEN = "firebase_token"
CONF_DEVICE_GUID = "device_guid"
