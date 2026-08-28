import os
import json
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_credential(env_var, json_key):
    # 1. Environment Variable
    val = os.getenv(env_var)
    if val:
        return val
    
    # 2. JSON File (data/credentials.json)
    try:
        creds_path = os.path.join('data', 'credentials.json')
        if os.path.exists(creds_path):
            with open(creds_path, 'r') as f:
                data = json.load(f)
                return data.get(json_key)
    except Exception:
        pass
        
    return None

# Withings Credentials
WITHINGS_CLIENT_ID = get_credential('WITHINGS_CLIENT_ID', 'withings_client_id')
WITHINGS_CLIENT_SECRET = get_credential('WITHINGS_CLIENT_SECRET', 'withings_client_secret')
# This must match what you set in the Withings Developer Dashboard
# Priority: Env Var -> JSON File -> Default Localhost
WITHINGS_REDIRECT_URI = get_credential('WITHINGS_REDIRECT_URI', 'withings_redirect_uri')
if not WITHINGS_REDIRECT_URI:
    WITHINGS_REDIRECT_URI = 'http://localhost:5000/auth/withings/callback'

_withings_enabled = get_credential('WITHINGS_ENABLED', 'withings_enabled')
if _withings_enabled is None:
    WITHINGS_ENABLED = True
else:
    WITHINGS_ENABLED = bool(_withings_enabled) if isinstance(_withings_enabled, bool) else str(_withings_enabled).lower() in ('true', '1', 'yes', 'on')

# Garmin Credentials
GARMIN_EMAIL = get_credential('GARMIN_EMAIL', 'garmin_email')
GARMIN_PASSWORD = get_credential('GARMIN_PASSWORD', 'garmin_password')

# Xiaomi S400 Credentials & Profile
XIAOMI_MAC = get_credential('XIAOMI_MAC', 'xiaomi_mac')
XIAOMI_BINDKEY = get_credential('XIAOMI_BINDKEY', 'xiaomi_bindkey')
XIAOMI_TOKEN = get_credential('XIAOMI_TOKEN', 'xiaomi_token')
XIAOMI_SEX = get_credential('XIAOMI_SEX', 'xiaomi_sex') or 'male'
XIAOMI_AGE = int(get_credential('XIAOMI_AGE', 'xiaomi_age') or 30)
XIAOMI_HEIGHT = int(get_credential('XIAOMI_HEIGHT', 'xiaomi_height') or 180)

_target_w = get_credential('XIAOMI_TARGET_WEIGHT', 'xiaomi_target_weight')
XIAOMI_TARGET_WEIGHT = float(_target_w) if _target_w not in (None, '') else None

_tol = get_credential('XIAOMI_WEIGHT_TOLERANCE', 'xiaomi_weight_tolerance')
XIAOMI_WEIGHT_TOLERANCE = float(_tol) if _tol not in (None, '') else 5.0

_enabled = get_credential('XIAOMI_ENABLED', 'xiaomi_enabled')
XIAOMI_ENABLED = bool(_enabled) if isinstance(_enabled, bool) else str(_enabled).lower() in ('true', '1', 'yes', 'on')

def reload_config():
    """Reloads variables from environment and credentials.json."""
    global WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, WITHINGS_REDIRECT_URI, WITHINGS_ENABLED
    global GARMIN_EMAIL, GARMIN_PASSWORD
    global XIAOMI_MAC, XIAOMI_BINDKEY, XIAOMI_TOKEN, XIAOMI_SEX, XIAOMI_AGE, XIAOMI_HEIGHT
    global XIAOMI_TARGET_WEIGHT, XIAOMI_WEIGHT_TOLERANCE, XIAOMI_ENABLED

    WITHINGS_CLIENT_ID = get_credential('WITHINGS_CLIENT_ID', 'withings_client_id')
    WITHINGS_CLIENT_SECRET = get_credential('WITHINGS_CLIENT_SECRET', 'withings_client_secret')
    WITHINGS_REDIRECT_URI = get_credential('WITHINGS_REDIRECT_URI', 'withings_redirect_uri') or 'http://localhost:5000/auth/withings/callback'

    _w_enabled = get_credential('WITHINGS_ENABLED', 'withings_enabled')
    if _w_enabled is None:
        WITHINGS_ENABLED = True
    else:
        WITHINGS_ENABLED = bool(_w_enabled) if isinstance(_w_enabled, bool) else str(_w_enabled).lower() in ('true', '1', 'yes', 'on')

    GARMIN_EMAIL = get_credential('GARMIN_EMAIL', 'garmin_email')
    GARMIN_PASSWORD = get_credential('GARMIN_PASSWORD', 'garmin_password')

    XIAOMI_MAC = get_credential('XIAOMI_MAC', 'xiaomi_mac')
    XIAOMI_BINDKEY = get_credential('XIAOMI_BINDKEY', 'xiaomi_bindkey')
    XIAOMI_TOKEN = get_credential('XIAOMI_TOKEN', 'xiaomi_token')
    XIAOMI_SEX = get_credential('XIAOMI_SEX', 'xiaomi_sex') or 'male'
    XIAOMI_AGE = int(get_credential('XIAOMI_AGE', 'xiaomi_age') or 30)
    XIAOMI_HEIGHT = int(get_credential('XIAOMI_HEIGHT', 'xiaomi_height') or 180)

    _target_w = get_credential('XIAOMI_TARGET_WEIGHT', 'xiaomi_target_weight')
    XIAOMI_TARGET_WEIGHT = float(_target_w) if _target_w not in (None, '') else None

    _tol = get_credential('XIAOMI_WEIGHT_TOLERANCE', 'xiaomi_weight_tolerance')
    XIAOMI_WEIGHT_TOLERANCE = float(_tol) if _tol not in (None, '') else 5.0

    _enabled = get_credential('XIAOMI_ENABLED', 'xiaomi_enabled')
    XIAOMI_ENABLED = bool(_enabled) if isinstance(_enabled, bool) else str(_enabled).lower() in ('true', '1', 'yes', 'on')


