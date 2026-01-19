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

# Garmin Credentials
GARMIN_EMAIL = get_credential('GARMIN_EMAIL', 'garmin_email')
GARMIN_PASSWORD = get_credential('GARMIN_PASSWORD', 'garmin_password')
