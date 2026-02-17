
import os
import sys
import json
import requests
import datetime
import pickle
import time
from config import WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, WITHINGS_REDIRECT_URI

# Setup authentication reuse
DATA_DIR = "data"
TOKEN_FILE = os.path.join(DATA_DIR, "withings_tokens.pkl")

def load_credentials():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as f:
            return pickle.load(f)
    return None

def refresh_token(token_data):
    print("Refreshing token...")
    url = "https://wbsapi.withings.net/v2/oauth2"
    data = {
        'action': 'requesttoken',
        'grant_type': 'refresh_token',
        'client_id': WITHINGS_CLIENT_ID,
        'client_secret': WITHINGS_CLIENT_SECRET,
        'refresh_token': token_data.get('refresh_token')
    }
    response = requests.post(url, data=data)
    if response.status_code == 200:
        body = response.json().get('body')
        if body:
            with open(TOKEN_FILE, 'wb') as f:
                pickle.dump(body, f)
            return body
    print(f"Failed to refresh token: {response.text}")
    return token_data

def run_diagnostics():
    log_lines = []
    def log(msg):
        print(msg)
        log_lines.append(str(msg))

    log("Running Withings API Diagnostics...")
    token_data = load_credentials()
    if not token_data:
        log("No tokens found. Authenticate via main app first.")
        return

    # Refresh to be safe
    token_data = refresh_token(token_data)
    access_token = token_data['access_token']
    
    url = "https://wbsapi.withings.net/measure"
    headers = {'Authorization': f'Bearer {access_token}'}
    
    # 1. Fetch Jan 2025 target range
    log("\n--- TEST 1: Fetching Jan 2025 (Target Range) ---")
    
    # 2025-01-01
    start_date = 1735689600 
    # 2025-02-01
    end_date = 1738368000
    
    # Actually, verify timestamps with logic to be safe
    dt_start = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    dt_end = datetime.datetime(2025, 2, 1, tzinfo=datetime.timezone.utc)
    
    start_date = int(dt_start.timestamp())
    end_date = int(dt_end.timestamp())

    log(f"Querying from {dt_start} ({start_date}) to {dt_end} ({end_date})")

    params = {
        'action': 'getmeas',
        'startdate': start_date,
        'enddate': end_date
    }
    
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    
    status = data.get('status')
    matches = []
    all_types = set()
    
    if status == 0:
        groups = data.get('body', {}).get('measuregrps', [])
        log(f"Found {len(groups)} measurement groups.")
        for grp in groups:
            types = [m['type'] for m in grp['measures']]
            all_types.update(types)
            # Check for BP
            if 9 in types or 10 in types or 11 in types:
                date_str = datetime.datetime.fromtimestamp(grp['date']).strftime('%Y-%m-%d %H:%M:%S')
                matches.append(f"Date: {date_str}, Types: {types}, Category: {grp.get('category')}")
                
        log(f"Unique Measure Types Found: {sorted(list(all_types))}")
        if matches:
            log(f"FOUND BP DATA in {len(matches)} groups:")
            for m in matches:
                log("  " + m)
        else:
            log("NO BP DATA FOUND (Types 9, 10, 11) in default category.")
    else:
        log(f"API Error: {status}")

    # 2. Fetch last 60 days, Category 2 (User Objectives)
    log("\n--- TEST 2: Fetching last 60 days (Category 2 - User Objectives) ---")
    params['category'] = 2
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    if data.get('status') == 0:
        groups = data.get('body', {}).get('measuregrps', [])
        log(f"Found {len(groups)} measurement groups in Category 2.")
        
        matches_cat2 = []
        for grp in groups:
            types = [m['type'] for m in grp['measures']]
            if 9 in types or 10 in types or 11 in types:
                 matches_cat2.append(types)
        
        if matches_cat2:
             log(f"Found BP data in Category 2: {len(matches_cat2)} groups")
        else:
             log("No BP data in Category 2.")
             
    # Write to file
    with open("diagnostic_log.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    print("Log written to diagnostic_log.txt")

if __name__ == "__main__":
    run_diagnostics()
