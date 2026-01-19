import os
import sys
import time
import requests
import json
import pickle
import urllib.parse
from datetime import datetime, timezone
import tzlocal
from config import WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, WITHINGS_REDIRECT_URI, GARMIN_EMAIL, GARMIN_PASSWORD
import config
from garminconnect import Garmin

# Ensure data directory exists
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    try:
        os.makedirs(DATA_DIR)
    except:
        pass # Created by docker volume usually

TOKEN_FILE = os.path.join(DATA_DIR, "withings_tokens.pkl")

def save_credentials(token_data):
    """Saves the token data (dict) to a file."""
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, 'wb') as f:
            pickle.dump(token_data, f)
        print("Credentials saved successfully.")
    except Exception as e:
        print(f"Error saving credentials: {e}")

def load_credentials():
    """Loads token data from file if it exists."""
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            print(f"Error loading credentials: {e}")
    return None

class SimpleWithingsAuth:
    """
    Simple class to handle Withings OAuth2 flow without external library dependencies.
    """
    AUTH_URL = "https://account.withings.com/oauth2_user/authorize2"
    TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"

    def __init__(self, client_id, client_secret, redirect_uri):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def get_authorize_url(self):
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': 'user.metrics,user.info,user.activity',
            'state': 'init_auth'
        }
        return f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

    def get_credentials(self, code):
        data = {
            'action': 'requesttoken',
            'grant_type': 'authorization_code',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': code,
            'redirect_uri': self.redirect_uri
        }
        response = requests.post(self.TOKEN_URL, data=data)
        if response.status_code == 200:
            resp_json = response.json()
            if resp_json.get('status') == 0:
                print("Token exchange successful.")
                return resp_json.get('body')
            else:
                raise Exception(f"Token exchange failed: {resp_json}")
        else:
            raise Exception(f"HTTP Error during token exchange: {response.status_code} - {response.text}")

    def refresh_token(self, refresh_token):
        data = {
            'action': 'requesttoken',
            'grant_type': 'refresh_token',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': refresh_token
        }
        response = requests.post(self.TOKEN_URL, data=data)
        if response.status_code == 200:
            resp_json = response.json()
            if resp_json.get('status') == 0:
                print("Token refresh successful.")
                return resp_json.get('body')
            else:
                raise Exception(f"Token refresh failed: {resp_json}")
        else:
            raise Exception(f"HTTP Error during refresh: {response.status_code} - {response.text}")

def get_withings_credentials():
    auth = SimpleWithingsAuth(config.WITHINGS_CLIENT_ID, config.WITHINGS_CLIENT_SECRET, config.WITHINGS_REDIRECT_URI)
    
    authorize_url = auth.get_authorize_url()
    print(f"\nPlease visit this URL to authorize the app:\n{authorize_url}\n")
    
    try:
        code_input = input("Enter the code from the callback URL: ").strip()
    except EOFError:
        print("\n[ERROR] Authentication required.")
        print("Required credentials missing or invalid.")
        print("Please visit the Web UI (Credentials section) to authorize the application.")
        print("If running in Docker, ensure port 5000 is mapped and accessible.")
        raise
    
    # Allow user to paste the full URL
    if "code=" in code_input:
        try:
            parsed = urllib.parse.urlparse(code_input)
            query_params = urllib.parse.parse_qs(parsed.query)
            code = query_params.get('code', [None])[0]
            if not code:
                # Fallback if code is empty but 'code=' was present
                code = code_input 
        except:
             code = code_input
    else:
        code = code_input
    
    token_data = auth.get_credentials(code)
    save_credentials(token_data)
    return token_data

def authenticate_withings():
    """Tries to load credentials, refreshing if necessary."""
    token_data = load_credentials()
    if token_data:
        print("Loaded saved credentials checking expiry/validity logic could be here...")
        # Start simple: just use them, if API fails (401), we might need logic to refresh using 'refresh_token'
        # For a nicer UX, we should probably check if access_token is valid or just try to refresh if it's been a while.
        # But for now, let's just return what we have.
        # Ideally, we should check expiry but Withings tokens last 3 hours.
        # Let's try to refresh immediately if we have a refresh token, to ensure we have a fresh access token.
        # This is safer than failing mid-operation.
        
        refresh_token = token_data.get('refresh_token')
        if refresh_token:
            try:
                print("Attempting to refresh token...")
                auth = SimpleWithingsAuth(config.WITHINGS_CLIENT_ID, config.WITHINGS_CLIENT_SECRET, config.WITHINGS_REDIRECT_URI)
                new_token_data = auth.refresh_token(refresh_token)
                save_credentials(new_token_data)
                return new_token_data
            except Exception as e:
                print(f"Token refresh failed ({e}), requesting new login.")
        else:
             print("No refresh token found, requesting new login.")
        
    return get_withings_credentials()

def get_measure_value(measure):
    """
    Helper to calculate the real value from value and unit.
    value * 10^unit
    """
    return measure['value'] * (10 ** measure['unit'])

def get_latest_height(access_token):
    """
    Fetches the latest height measurement to use for BMI calculation.
    """
    url = "https://wbsapi.withings.net/measure"
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {
        'action': 'getmeas',
        'meastype': '4', # Height
        'category': 1,
        'limit': 1
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 0:
                measuregrps = data.get('body', {}).get('measuregrps', [])
                if measuregrps:
                    for measure in measuregrps[0]['measures']:
                        if measure['type'] == 4:
                            return get_measure_value(measure) # Returns height in meters
    except Exception as e:
        print(f"Warning: Could not fetch height: {e}")
    return None

def sync_data(token_data, garmin_client):
    access_token = token_data['access_token']
    
    print("\nFetching latest height for BMI calculation...")
    user_height = get_latest_height(access_token)
    if user_height:
        print(f"  Found height: {user_height} m")
    else:
        print("  No height found. BMI will not be calculated.")

    print("\nFetching data from Withings...")
    
    url = "https://wbsapi.withings.net/measure"
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {
        'action': 'getmeas',
        'meastype': '1,6,76,77,88,12', # Weight, Fat Ratio, Muscle Mass, Hydration, Bone Mass, Visceral Fat
        'category': 1 
    }
    
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code != 200:
        print(f"Error fetching data from Withings: {response.text}")
        return
        
    data = response.json()
    
    if data.get('status') != 0:
        print(f"Withings API Error: {data}")
        return
        
    body = data.get('body', {})
    measuregrps = body.get('measuregrps', [])
    
    if not measuregrps:
        print("No measures found on Withings.")
        return

    print(f"Found {len(measuregrps)} measurement groups.")
    
    # Search for the latest group that has a weight measurement
    group = None
    for g in measuregrps:
        # Check if this group has a weight measure (type 1)
        # The API returns groups sorted by date descending (newest first) by default
        has_weight = False
        for m in g['measures']:
            if m['type'] == 1:
                has_weight = True
                break
        
        if has_weight:
            group = g
            break
            
    if not group:
         print("No measurement group with weight found.")
         return
    
    dt = datetime.fromtimestamp(group['date'], timezone.utc)
    
    # Convert to local time
    local_tz = tzlocal.get_localzone()
    dt_local = dt.astimezone(local_tz)
    
    print(f"\nProcessing measurement for {dt} (UTC) -> {dt_local} (Local)...")
    
    weight = None
    fat_ratio = None
    muscle_mass = None
    hydration = None
    bone_mass = None
    visceral_fat = None
    
    for measure in group['measures']:
        val = get_measure_value(measure)
        type_code = measure['type']
        
        if type_code == 1: # Weight (kg)
            weight = val
        elif type_code == 6: # Fat Ratio (%)
            fat_ratio = val
        elif type_code == 76: # Muscle Mass (kg)
            muscle_mass = val
        elif type_code == 77: # Hydration (kg) or mass?
            hydration = val
        elif type_code == 88: # Bone Mass (kg)
            bone_mass = val
        elif type_code == 12: # Visceral Fat
            visceral_fat = val
            
    if weight:
        print(f"  Weight: {weight} kg")
        if fat_ratio: print(f"  Fat Ratio: {fat_ratio} %")
        if muscle_mass: print(f"  Muscle Mass: {muscle_mass} kg")
        if hydration: print(f"  Hydration: {hydration} kg")
        
        percent_hydration = None
        if hydration and weight:
            percent_hydration = (hydration / weight) * 100
            
        bmi = None
        if user_height:
            bmi = weight / (user_height * user_height)
            print(f"  Calculated BMI: {bmi:.2f}")

        try:
            timestamp_str = dt_local.isoformat()
            
            print(f"  Uploading to Garmin at {timestamp_str}...")
            
            garmin_client.add_body_composition(
                timestamp=timestamp_str,
                weight=weight,
                percent_fat=fat_ratio,
                percent_hydration=percent_hydration,
                visceral_fat_rating=visceral_fat,
                bone_mass=bone_mass,
                muscle_mass=muscle_mass,
                bmi=bmi
            )
            print(f"  Successfully synced to Garmin!")
        except Exception as e:
            print(f"  Failed to upload to Garmin: {e}")
    else:
        print("  Skipping group (No weight found).")

def main():
    print("Welcome to the Withings to Garmin Sync Tool!")
    
    if not config.WITHINGS_CLIENT_ID or not config.WITHINGS_CLIENT_SECRET:
        print("Error: Withings Credentials not found. Please configure your Withings credentials.")
        return
        
    if not config.GARMIN_EMAIL or not config.GARMIN_PASSWORD:
        print("Error: Garmin Credentials not found. Please configure your Garmin credentials.")
        return

    # 2. Authenticate Withings
    try:
        print("Connecting to Withings...")
        token_data = authenticate_withings()
    except Exception as e:
        print(f"Withings Auth Failed: {e}")
        return

    # 3. Authenticate Garmin
    try:
        print("Connecting to Garmin...")
        garmin = Garmin(config.GARMIN_EMAIL, config.GARMIN_PASSWORD)
        garmin.login()
    except Exception as e:
        print(f"Garmin Auth Failed: {e}")
        return

    # 4. Sync
    sync_data(token_data, garmin)
    print("\nSync Complete!")

if __name__ == "__main__":
    main()
