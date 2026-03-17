"""Constants for the pyvivosun library."""

BASE_URL = "https://api-prod.next.vivosun.com"

# API endpoints
LOGIN_ENDPOINT = "/user/login"
DEVICE_LIST_ENDPOINT = "/iot/device/getTotalList"
AWS_IDENTITY_ENDPOINT = "/iot/user/awsIdentity"
POINT_LOG_ENDPOINT = "/iot/data/getPointLog"

# Cognito
COGNITO_REGION = "us-east-2"
COGNITO_URL = f"https://cognito-identity.{COGNITO_REGION}.amazonaws.com/"

# Login app identifiers
SP_APP_ID = "com.vivosun.android"

# MQTT shadow topic patterns
SHADOW_GET = "$aws/things/{client_id}/shadow/get"
SHADOW_GET_ACCEPTED = "$aws/things/{client_id}/shadow/get/accepted"
SHADOW_UPDATE = "$aws/things/{client_id}/shadow/update"
SHADOW_UPDATE_ACCEPTED = "$aws/things/{client_id}/shadow/update/accepted"
SHADOW_UPDATE_DELTA = "$aws/things/{client_id}/shadow/update/delta"

# Point log time levels (aggregation granularity)
TIME_LEVEL_ONE_MINUTE = "ONE_MINUTE"
TIME_LEVEL_FIVE_MINUTES = "FIVE_MINUTES"
TIME_LEVEL_FIFTEEN_MINUTES = "FIFTEEN_MINUTES"
TIME_LEVEL_THIRTY_MINUTES = "THIRTY_MINUTES"
TIME_LEVEL_ONE_HOUR = "ONE_HOUR"

# Scaling
SCALE_DIVISOR = 100
SENTINEL_VALUE = -6666

# Light
MIN_LIGHT_LEVEL = 25
MAX_LIGHT_LEVEL = 100

# Fan
MIN_FAN_LEVEL = 0
MAX_FAN_LEVEL = 10
NATURAL_WIND_VALUE = 200

# Humidifier
MIN_HUMIDIFIER_LEVEL = 0
MAX_HUMIDIFIER_LEVEL = 10

# Heater
MIN_HEATER_LEVEL = 0
MAX_HEATER_LEVEL = 10

# Sensor field names (REST point log keys)
SENSOR_KEY_IN_TEMP = "inTemp"
SENSOR_KEY_IN_HUMI = "inHumi"
SENSOR_KEY_IN_VPD = "inVpd"
SENSOR_KEY_OUT_TEMP = "outTemp"
SENSOR_KEY_OUT_HUMI = "outHumi"
SENSOR_KEY_OUT_VPD = "outVpd"
SENSOR_KEY_PROBE_TEMP = "pTemp"
SENSOR_KEY_PROBE_HUMI = "pHumi"
SENSOR_KEY_PROBE_VPD = "pVpd"
SENSOR_KEY_WATER_LEVEL = "waterLv"
SENSOR_KEY_CORE_TEMP = "coreTemp"
SENSOR_KEY_RSSI = "rssi"

# Shadow keys
SHADOW_KEY_LIGHT = "light"
SHADOW_KEY_CFAN = "cFan"
SHADOW_KEY_DFAN = "dFan"
SHADOW_KEY_HUMIDIFIER = "hmdf"
SHADOW_KEY_HEATER = "heat"

# Timeouts
REQUEST_TIMEOUT = 15  # seconds

# Credential refresh
AWS_CREDENTIAL_REFRESH_SKEW = 300  # 5 minutes before expiry
AWS_CREDENTIAL_REFRESH_INTERVAL = 2700  # 45 minutes
