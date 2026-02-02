import sys
import time
import requests
import json
from datetime import datetime, timezone, timedelta
import tzlocal
import config
from garminconnect import Garmin
# Import auth logic from sync_app to reuse the manual implementation and token persistence
from sync_app import authenticate_withings, save_credentials, get_withings_credentials

def get_measure_value(measure):
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
        print(f"Warning: Could not fetch height. Error type: {type(e).__name__}")
    return None

def sync_data(token_data, garmin_client, days=30, progress_callback=None):
    access_token = token_data['access_token']
    
    print("\nFetching latest height for BMI calculation...")
    user_height = get_latest_height(access_token)
    if user_height:
        print(f"  Found height: {user_height} m")
    else:
        print("  No height found. BMI will not be calculated.")

    print(f"\nFetching data from Withings for the last {days} days...")
    
    # Calculate start date (Epoch)
    now = datetime.now(timezone.utc)
    start_date_obj = now - timedelta(days=days)
    startdate = int(start_date_obj.timestamp())
    
    url = "https://wbsapi.withings.net/measure"
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {
        'action': 'getmeas',
        'meastype': '1,6,76,77,88,12', 
        'category': 1,
        'startdate': startdate
    }
    
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code != 200:
        print(f"Error fetching data from Withings. Status: {response.status_code}")
        return
        
    data = response.json()
    
    if data.get('status') != 0:
        print(f"Withings API Error. Status: {data.get('status')}")
        return
        
    body = data.get('body', {})
    measuregrps = body.get('measuregrps', [])
    
    if not measuregrps:
        print(f"No measures found on Withings for the last {days} days.")
        return

    # Filter to only keep groups that have weight data (type 1)
    valid_groups = []
    for group in measuregrps:
        has_weight = False
        for m in group['measures']:
            if m['type'] == 1:
                has_weight = True
                break
        if has_weight:
            valid_groups.append(group)
            
    measuregrps = valid_groups
    total_groups = len(measuregrps)
    print(f"Found {total_groups} measurement groups with weight data.")
    
    # Reverse to process from Oldest to Newest
    measuregrps.reverse()
    
    success_count = 0
    fail_count = 0
    
    # Init local timezone
    local_tz = tzlocal.get_localzone()
    
    # Process ALL groups found
    for i, group in enumerate(measuregrps):
        # Progress Update
        if progress_callback:
            progress_callback(i + 1, total_groups)

        dt = datetime.fromtimestamp(group['date'], timezone.utc)
        
        # Convert to local time
        dt_local = dt.astimezone(local_tz)
        
        print(f"\nProcessing measurement {i+1}/{total_groups} for {dt} (UTC) -> {dt_local} (Local)...")
        
        weight = None
        fat_ratio = None
        muscle_mass = None
        hydration = None
        bone_mass = None
        visceral_fat = None
        
        for measure in group['measures']:
            val = get_measure_value(measure)
            type_code = measure['type']
            
            if type_code == 1: weight = val
            elif type_code == 6: fat_ratio = val
            elif type_code == 76: muscle_mass = val
            elif type_code == 77: hydration = val
            elif type_code == 88: bone_mass = val
            elif type_code == 12: visceral_fat = val
            
        if weight:
            print(f"  Weight: {weight} kg")
            
            percent_hydration = None
            if hydration and weight:
                percent_hydration = (hydration / weight) * 100
            
            # Calculate BMI
            bmi = None
            if user_height:
                bmi = weight / (user_height * user_height)
                # print(f"  Calculated BMI: {bmi:.2f}")

            try:
                timestamp_str = dt_local.isoformat()
                
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
                print(f"  Successfully synced.")
                success_count += 1
            except Exception as e:
                print(f"  Failed to upload. Error type: {type(e).__name__}")
                fail_count += 1
                
            # Avoid hitting rate limits (Garmin doesn't like rapid fire requests sometimes)
            time.sleep(1) # Be nice
        else:
            print("  Skipping group (No weight found).")
            
    print(f"\nBatch Sync Complete. Success: {success_count}, Failures: {fail_count}")

def run_historical_sync(days=30, progress_callback=None):
    print(f"Withings to Garmin Sync Tool - {days} Day Batch")
    
    if not config.WITHINGS_CLIENT_ID or not config.WITHINGS_CLIENT_SECRET:
        print("Error: Withings Credentials not found in .env")
        return
        
    if not config.GARMIN_EMAIL or not config.GARMIN_PASSWORD:
        print("Error: Garmin Credentials not found in .env")
        return

    try:
        # Use simple auth from sync_app
        print("Connecting to Withings...")
        token_data = authenticate_withings()
    except Exception as e:
        print(f"Withings Auth Failed. Error type: {type(e).__name__}")
        return

    try:
        print("Connecting to Garmin...")
        garmin = Garmin(config.GARMIN_EMAIL, config.GARMIN_PASSWORD)
        import os
        token_dir = os.path.join("data", '.garth') 
        # Ensure it exists
        if not os.path.exists(token_dir):
            os.makedirs(token_dir, exist_ok=True)
            
        token_file = os.path.join(token_dir, 'oauth1_token.json')
        if os.path.exists(token_file):
            garmin.login(tokenstore=token_dir)
        else:
             print("Token file not found, logging in via default store...")
             garmin.login()
             garmin.garth.dump(token_dir)
    except Exception as e:
        print(f"Garmin Auth Failed. Check credentials. Error type: {type(e).__name__}")
        return

    sync_data(token_data, garmin, days=days, progress_callback=progress_callback)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Sync historical Withings data to Garmin.')
    parser.add_argument('--days', type=int, default=30, help='Number of days to sync (default: 30)')
    args = parser.parse_args()
    
    run_historical_sync(args.days)

if __name__ == "__main__":
    main()
