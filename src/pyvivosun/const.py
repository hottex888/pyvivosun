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

# Timeouts
REQUEST_TIMEOUT = 15  # seconds

# Credential refresh
AWS_CREDENTIAL_REFRESH_SKEW = 300  # 5 minutes before expiry
AWS_CREDENTIAL_REFRESH_INTERVAL = 2700  # 45 minutes
