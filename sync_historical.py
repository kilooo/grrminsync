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

def sync_data(token_data, garmin_client, days=30, start_date=None, end_date=None, progress_callback=None):
    access_token = token_data['access_token']
    
    print("\nFetching latest height for BMI calculation...")
    user_height = get_latest_height(access_token)
    if user_height:
        print(f"  Found height: {user_height} m")
    else:
        print("  No height found. BMI will not be calculated.")

    # Determine Start/End Timestamps
    startdate = None
    enddate = None
    
    if start_date:
        # Expecting timestamp intergers
        startdate = start_date
        enddate = end_date # Optional, defaults to now if None
        
        # Friendly logging
        sd_str = datetime.fromtimestamp(startdate).strftime('%Y-%m-%d')
        ed_str = datetime.fromtimestamp(enddate).strftime('%Y-%m-%d') if enddate else "Now"
        print(f"\nFetching data from Withings from {sd_str} to {ed_str}...")
        
    else:
        # Legacy behavior: Last X days
        print(f"\nFetching data from Withings for the last {days} days...")
        now = datetime.now(timezone.utc)
        start_date_obj = now - timedelta(days=days)
        startdate = int(start_date_obj.timestamp())
    
    url = "https://wbsapi.withings.net/measure"
    headers = {'Authorization': f'Bearer {access_token}'}
    # DEBUG: Request EVERYTHING to see where BP is hiding
    params = {
        'action': 'getmeas',
        # 'meastype': '1,6,76,77,88,12,9,10,11',  # Commenting out to fetch ALL types
        # 'category': 1, # Fetch both Real (1) and User Objectives (2)
        'startdate': startdate
    }
    
    if enddate:
        params['enddate'] = enddate
    
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
        print(f"No measures found on Withings for the requested period.")
        return

    # Filter to only keep groups that have weight data (type 1) OR blood pressure (type 9, 10)
    valid_groups = []
    for group in measuregrps:
        has_weight = False
        has_bp = False
        for m in group['measures']:
            if m['type'] == 1:
                has_weight = True
            if m['type'] in [9, 10]:
                has_bp = True
        
        if has_weight or has_bp:
            valid_groups.append(group)
            
    measuregrps = valid_groups
    total_groups = len(measuregrps)
    print(f"Found {total_groups} valid measurement groups (Weight or BP).")
    
    # Reverse to process from Oldest to Newest
    measuregrps.reverse()
    
    success_count = 0
    fail_count = 0
    
    # Init local timezone
    local_tz = tzlocal.get_localzone()
    
    # Process ALL groups found
    for i, group in enumerate(measuregrps):
        if progress_callback:
            progress_callback(i + 1, total_groups)

        dt = datetime.fromtimestamp(group['date'], timezone.utc)
        
        # Convert to local time
        dt_local = dt.astimezone(local_tz)
        
        # DEBUG: Inspect types in this group
        types_in_group = [m['type'] for m in group['measures']]
        print(f"\nProcessing measurement {i+1}/{total_groups} for {dt} (UTC) -> {dt_local} (Local)... [Types: {types_in_group}]")
        
        weight = None
        fat_ratio = None
        muscle_mass = None
        hydration = None
        bone_mass = None
        visceral_fat = None
        
        
        diastolic = None
        systolic = None
        heart_rate = None

        for measure in group['measures']:
            val = get_measure_value(measure)
            type_code = measure['type']
            
            if type_code == 1: weight = val
            elif type_code == 6: fat_ratio = val
            elif type_code == 76: muscle_mass = val
            elif type_code == 77: hydration = val
            elif type_code == 88: bone_mass = val
            elif type_code == 12: visceral_fat = val
            elif type_code == 9: diastolic = int(val)
            elif type_code == 10: systolic = int(val)
            elif type_code == 11: heart_rate = int(val)
            
        group_success = False
        
        # --- UPLOAD WEIGHT ---
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
                print(f"  Successfully synced Weight.")
                group_success = True
            except Exception as e:
                print(f"  Failed to upload Weight. Error type: {type(e).__name__}")
        
        # --- UPLOAD BLOOD PRESSURE ---
        if systolic and diastolic:
            print(f"  BP: {systolic}/{diastolic} mmHg, HR: {heart_rate}")
            try:
                garmin_client.set_blood_pressure(
                    systolic=systolic,
                    diastolic=diastolic,
                    pulse=heart_rate,
                    timestamp=dt_local.isoformat()
                )
                print(f"  Successfully synced Blood Pressure.")
                group_success = True
            except Exception as e:
                print(f"  Failed to upload Blood Pressure. Error type: {type(e).__name__}")

        if group_success:
            success_count += 1
        else:
            if not weight and not (systolic and diastolic):
                print("  Skipping group (No valid weight or BP data).")
            else:
                fail_count += 1

        # Avoid hitting rate limits (Garmin doesn't like rapid fire requests sometimes)
        time.sleep(1) # Be nice
            
    print(f"\nBatch Sync Complete. Success (Groups): {success_count}, Failures/Partial: {fail_count}")

    # If dates provided, parse them
    start_ts = None
    end_ts = None
    
    # If called via kwargs (e.g. from server.py wrapper which might pass args differently)
    # But usually run_historical_sync(days=X) or run_historical_sync(from_date=Y, to_date=Z)
    # Let's adjust signature of run_historical_sync to be more flexible
    pass

def run_historical_sync(days=30, from_date=None, to_date=None, progress_callback=None):
    if from_date:
        print(f"Withings to Garmin Sync Tool - Date Range: {from_date} to {to_date or 'Now'}")
    else:
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

    # Parse Dates
    start_ts = None
    end_ts = None
    
    if from_date:
        try:
            # Assuming YYYY-MM-DD
            dt_start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            start_ts = int(dt_start.timestamp())
            
            if to_date:
                dt_end = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
                end_ts = int(dt_end.timestamp())
        except ValueError as e:
            print(f"Error parsing dates: {e}")
            return

    sync_data(token_data, garmin, days=days, start_date=start_ts, end_date=end_ts, progress_callback=progress_callback)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Sync historical Withings data to Garmin.')
    parser.add_argument('--days', type=int, default=30, help='Number of days to sync (default: 30)')
    args = parser.parse_args()
    
    run_historical_sync(args.days)

if __name__ == "__main__":
    main()
