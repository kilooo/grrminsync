from flask import Flask, render_template_string, request, jsonify, render_template
import sys
import io
import contextlib
import json
import os
import atexit
import time

# Force immediate log output
print("DEBUG: Server module loading...", flush=True)

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import sync_app
from datetime import datetime
import tzlocal
from config import WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, WITHINGS_REDIRECT_URI, GARMIN_EMAIL, GARMIN_PASSWORD

import sync_historical
import sqlite3
import threading

print("DEBUG: Imports complete. Initializing App...", flush=True)

app = Flask(__name__)
print("DEBUG: Flask app created.", flush=True)

# Scheduler Setup
try:
    # Explicitly use local timezone
    local_tz = tzlocal.get_localzone()
    scheduler = BackgroundScheduler(timezone=str(local_tz))
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    print(f"DEBUG: Scheduler started with timezone: {local_tz}", flush=True)
except Exception as e:
    print(f"DEBUG: Scheduler failed to start. Error type: {type(e).__name__}", flush=True)
    sys.exit(1)

# Database Setup
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    try:
        os.makedirs(DATA_DIR)
        print(f"DEBUG: Created data directory at {DATA_DIR}", flush=True)
    except Exception as e:
        print(f"DEBUG: Failed to create data directory. Error type: {type(e).__name__}", flush=True)

DB_PATH = os.path.join(DATA_DIR, "garmin_import.db")

def init_db():
    print(f"DEBUG: Initializing database at {DB_PATH}...", flush=True)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS sync_history
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, status TEXT, log TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS schedule_config
                         (id INTEGER PRIMARY KEY CHECK (id = 1), hour INTEGER, minute INTEGER, enabled BOOLEAN)''')
            conn.commit()
        print("DEBUG: Database initialized success.", flush=True)
    except Exception as e:
        print(f"DEBUG: Database initialization failed. Error type: {type(e).__name__}", flush=True)
        sys.exit(1)

init_db()

# Global progress state
SYNC_PROGRESS = {
    "status": "idle", # idle, running, completed, error
    "current": 0,
    "total": 0,
    "message": "",
    "log": ""
}

def save_schedule(hour, minute):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO schedule_config (id, hour, minute, enabled) VALUES (1, ?, ?, 1)", (hour, minute))
        conn.commit()

def load_schedule():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT hour, minute, enabled FROM schedule_config WHERE id=1")
        row = c.fetchone()
        if row:
            return {"hour": row[0], "minute": row[1], "enabled": bool(row[2])}
    return None

def append_history(status, log_output):
    """Appends a new entry to the history database, keeping only the last 50."""
    now_local = datetime.now(tzlocal.get_localzone())
    timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
    
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO sync_history (timestamp, status, log) VALUES (?, ?, ?)", (timestamp, status, log_output))
        
        # Keep only last 50
        c.execute("DELETE FROM sync_history WHERE id NOT IN (SELECT id FROM sync_history ORDER BY id DESC LIMIT 50)")
        conn.commit()

def get_sync_history():
    entries = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT timestamp, status, log FROM sync_history ORDER BY id DESC")
            rows = c.fetchall()
            entries = [dict(row) for row in rows]
    except Exception as e:
        print(f"Error reading history. Error type: {type(e).__name__}")
    return entries

def run_sync_logic(target_func=sync_app.main, progress_dict=None, *args, **kwargs):
    """Shared logic for running sync and capturing output. Optionally updates progress_dict['log'] live."""
    f = io.StringIO()
    status = "Failed"
    
    class LiveBuffer:
        def __init__(self, original_f, p_dict):
            self.f = original_f
            self.p_dict = p_dict
        def write(self, s):
            self.f.write(s)
            if self.p_dict is not None:
                self.p_dict['log'] += s
        def flush(self):
            self.f.flush()

    try:
        buffer = f
        if progress_dict is not None:
            buffer = LiveBuffer(f, progress_dict)
            
        with contextlib.redirect_stdout(buffer):
            target_func(*args, **kwargs)
        status = "Success"
        output = f.getvalue()
        if "Error" in output or "Failed" in output or "Traceback" in output:
             status = "Failed"
             
    except Exception as e:
        output = f.getvalue() + f"\nBIG ERROR: {type(e).__name__}"
        status = "Failed"
        if progress_dict is not None:
            progress_dict['log'] = output
        
    return status, output

def scheduled_sync_job():
    print(f"Running scheduled sync...")
    status, output = run_sync_logic(target_func=sync_app.main)
    append_history(f"Scheduled ({status})", output)
    print(f"Scheduled sync finished: {status}")

# Restore schedule on startup
print("DEBUG: Attempting to restore schedule...", flush=True)
try:
    saved_schema = load_schedule()
    if saved_schema and saved_schema.get('enabled'):
        h = saved_schema['hour']
        m = saved_schema['minute']
        scheduler.add_job(
            func=scheduled_sync_job,
            trigger=CronTrigger(hour=h, minute=m),
            id='daily_sync',
            name='daily_sync_job',
            replace_existing=True
        )
        print(f"DEBUG: Restored schedule: Daily at {h}:{m:02d}", flush=True)
    else:
        print("DEBUG: No active schedule to restore.", flush=True)
except Exception as e:
    print(f"DEBUG: Failed to restore schedule. Error type: {type(e).__name__}", flush=True)
    # Don't exit, just continue without schedule

@app.route('/')
def index():
    history = get_sync_history()[:3]
    return render_template('home.html', active_page='home', history=history)

@app.route('/credentials')
def credentials_page():
    return render_template('credentials.html', active_page='credentials')

@app.route('/history')
def view_history():
    history = get_sync_history()
    return render_template('history.html', history=history, active_page='history')

@app.route('/historical')
def historical_page():
    return render_template('historical.html', active_page='historical')

@app.route('/manual')
def manual_entry_page():
    return render_template('manual.html', active_page='manual')

def _run_sync_thread(days):
    global SYNC_PROGRESS
    
    # Callback to update granular progress
    def progress_callback(current, total):
        SYNC_PROGRESS['status'] = 'running'
        SYNC_PROGRESS['current'] = current
        SYNC_PROGRESS['total'] = total
        SYNC_PROGRESS['message'] = "Syncing measurements..."
        
    print(f"Starting background sync for {days} days")
    
    # Reset State
    SYNC_PROGRESS = {
        "status": "running",
        "current": 0,
        "total": 0,
        "message": "Initializing...",
        "log": ""
    }
    
    # Run Logic
    status, output = run_sync_logic(
        sync_historical.run_historical_sync, 
        progress_dict=SYNC_PROGRESS,
        days=days, 
        progress_callback=progress_callback
    )
    
    # Save to history
    append_history(f"Historical {days}d ({status})", output)
    
    # Update Final State
    SYNC_PROGRESS['status'] = status # "Success" or "Failed"
    SYNC_PROGRESS['log'] = output
    print(f"Background sync finished: {status}")

@app.route('/historical/sync', methods=['POST'])
def run_historical_sync_endpoint():
    global SYNC_PROGRESS
    
    if SYNC_PROGRESS['status'] == 'running':
        return jsonify({"status": "error", "message": "A sync job is already running."}), 400

    data = request.json
    days = data.get('days', 30)
    
    # Start Thread
    t = threading.Thread(target=_run_sync_thread, args=(days,))
    t.start()
    
    return jsonify({"status": "started", "message": "Sync started in background"})

@app.route('/progress')
def get_progress():
    return jsonify(SYNC_PROGRESS)

@app.route('/sync', methods=['POST'])
def run_sync():
    # Helper for manual sync to also block manual runs if a historical one is running?
    # For now, let's allow them to overlap or fail naturally, but ideally we should lock.
    # But simple is fine.
    
    status, output = run_sync_logic(sync_app.main)
    
    # Save to history
    append_history(f"Manual ({status})", output)
    
    return jsonify({"status": status, "output": output})

@app.route('/manual/sync', methods=['POST'])
def run_manual_sync():
    data = request.json
    
    try:
        weight = float(data.get('weight'))
        fat_ratio = float(data.get('fat_ratio')) if data.get('fat_ratio') else None
        muscle_mass = float(data.get('muscle_mass')) if data.get('muscle_mass') else None
        bone_mass = float(data.get('bone_mass')) if data.get('bone_mass') else None
        hydration = float(data.get('hydration')) if data.get('hydration') else None
        bmi = float(data.get('bmi')) if data.get('bmi') else None
        timestamp = data.get('timestamp') # ISO format expected or None
        
        # Handle Unit Conversion
        unit = data.get('selected_unit', 'kg')
        if unit == 'lbs':
            # 1 lb = 0.45359237 kg
            lb_to_kg = 0.45359237
            weight *= lb_to_kg
            if muscle_mass: muscle_mass *= lb_to_kg
            if bone_mass: bone_mass *= lb_to_kg
            print(f"Converted lbs to kg: Weight={weight:.2f}")
        
        # If hydration is mass and weight is provided, convert to % for Garmin?
        # Garmin API usually expects percent_hydration.
        # Let's check if the user provides hydration as a percentage or mass.
        # We'll assume percentage for now, or add a toggle.
        
        # Garmin login can take 5-10 seconds.
        print(f"DEBUG: Starting manual upload. Timestamp={timestamp}, Weight={weight} ({unit})")
        
        # Actually, let's just do it synchronously for now to keep it simple, 
        # but the UI will show a loading spinner.
        
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            sync_app.upload_manual_data(
                weight=weight,
                fat_ratio=fat_ratio,
                muscle_mass=muscle_mass,
                bone_mass=bone_mass,
                hydration_percent=hydration,
                bmi=bmi,
                timestamp=timestamp
            )
        status = "Success"
        output = f.getvalue() or "Manual sync successful."
        append_history(f"Manual Entry ({status})", output)
        
        return jsonify({"status": status, "output": output})
        
    except Exception as e:
        error_msg = f"Failed. Error type: {type(e).__name__}"
        append_history("Manual Entry (Failed)", error_msg)
        return jsonify({"status": "Failed", "output": error_msg}), 500

@app.route('/schedule', methods=['GET'])
def get_schedule():
    conf = load_schedule()
    result = {"timezone": str(tzlocal.get_localzone())}
    if conf and conf.get('enabled'):
        result.update(conf)
        return jsonify(result)
    result["enabled"] = False
    return jsonify(result)

@app.route('/schedule', methods=['POST'])
def set_schedule_endpoint():
    data = request.json
    h = data.get('hour')
    m = data.get('minute')
    
    if h is None or m is None:
        return jsonify({"message": "Invalid time"}), 400
        
    scheduler.add_job(
        func=scheduled_sync_job,
        trigger=CronTrigger(hour=h, minute=m),
        id='daily_sync',
        name='daily_sync_job',
        replace_existing=True
    )
    
    save_schedule(h, m)
    return jsonify({"message": f"Scheduled daily sync at {h:02d}:{m:02d}"})

@app.route('/schedule', methods=['DELETE'])
def remove_schedule_endpoint():
    job = scheduler.get_job('daily_sync')
    if job:
        job.remove()
    
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("UPDATE schedule_config SET enabled=0 WHERE id=1")
        conn.commit()
        
    return jsonify({"message": "Schedule disabled"})

@app.route('/auth/withings/login')
def auth_withings_login():
    if not WITHINGS_CLIENT_ID or not WITHINGS_CLIENT_SECRET:
        return "Error: Withings Credentials not found in environment.", 500
        
    redirect_uri = WITHINGS_REDIRECT_URI
    
    # Dynamic Redirect URI Logic:
    # If the configured URI is localhost (default) but the user is accessing via a different host (IP/Domain),
    # assume they want to use the current host.
    # We only do this if they haven't explicitly set a custom URI (we assume 'localhost:5000...' is the default).
    if 'localhost' in redirect_uri and 'localhost' not in request.host:
        # Construct dynamic URI: http://<HOST>/auth/withings/callback
        # request.url_root gives 'http://<HOST>/'
        redirect_uri = request.url_root + 'auth/withings/callback'
        print(f"DEBUG: Using dynamic redirect URI: {redirect_uri}", flush=True)
    
    auth = sync_app.SimpleWithingsAuth(WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, redirect_uri)
    url = auth.get_authorize_url()
    
    return f"<script>window.location.href='{url}';</script>"

@app.route('/auth/withings/callback')
def auth_withings_callback():
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        return f"<h1>Auth Error</h1><p>Withings returned error: {error}</p><a href='/'>Back</a>"
        
    if not code:
        return "<h1>Error</h1><p>No code returned.</p><a href='/'>Back</a>"
        
    try:
        redirect_uri = WITHINGS_REDIRECT_URI
        
        # Mirror the dynamic logic from login to ensure matching URI for token exchange
        if 'localhost' in redirect_uri and 'localhost' not in request.host:
             redirect_uri = request.url_root + 'auth/withings/callback'
             print(f"DEBUG: Using dynamic redirect URI for callback: {redirect_uri}", flush=True)

        auth = sync_app.SimpleWithingsAuth(WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, redirect_uri)
        token_data = auth.get_credentials(code)
        
        # Save credentials using sync_app's helper
        sync_app.save_credentials(token_data)
        
        return "<h1>Success!</h1><p>Withings connected successfully.</p><script>setTimeout(function(){window.location.href='/';}, 2000);</script>"
        
    except Exception as e:
        return f"<h1>Setup Failed</h1><p>Error type: {type(e).__name__}</p><a href='/'>Back</a>"

@app.route('/config/withings', methods=['POST'])
def save_withings_config():
    client_id = request.form.get('client_id')
    client_secret = request.form.get('client_secret')
    redirect_uri = request.form.get('redirect_uri')
    
    if not client_id or not client_secret:
        return jsonify({"message": "Client ID and Secret are required"}), 400
        
    try:
        # Load existing creds (to preserve garmin if it exists)
        creds_path = os.path.join(DATA_DIR, 'credentials.json')
        creds = {}
        if os.path.exists(creds_path):
            try:
                with open(creds_path, 'r') as f:
                    creds = json.load(f)
            except:
                pass
                
        creds["withings_client_id"] = client_id
        creds["withings_client_secret"] = client_secret
        if redirect_uri:
            creds["withings_redirect_uri"] = redirect_uri
        
        with open(creds_path, 'w') as f:
            json.dump(creds, f)
            
        # Update running config
        import config
        config.WITHINGS_CLIENT_ID = client_id
        config.WITHINGS_CLIENT_SECRET = client_secret
        if redirect_uri:
            config.WITHINGS_REDIRECT_URI = redirect_uri
        
        # Also update global imports in this module
        global WITHINGS_CLIENT_ID, WITHINGS_CLIENT_SECRET, WITHINGS_REDIRECT_URI
        WITHINGS_CLIENT_ID = client_id
        WITHINGS_CLIENT_SECRET = client_secret
        if redirect_uri:
            WITHINGS_REDIRECT_URI = redirect_uri
        
        return jsonify({"message": "Withings Credentials Saved!"})
    except Exception as e:
        return jsonify({"message": f"Error saving. Error type: {type(e).__name__}"}), 500

@app.route('/config/garmin', methods=['POST'])
def save_garmin_config():
    email = request.form.get('email')
    password = request.form.get('password')
    
    if not email or not password:
        return jsonify({"message": "Email and Password are required"}), 400
        
    try:
        # Load existing creds (to preserve withings if it exists)
        creds_path = os.path.join(DATA_DIR, 'credentials.json')
        creds = {}
        if os.path.exists(creds_path):
            try:
                with open(creds_path, 'r') as f:
                    creds = json.load(f)
            except:
                pass
                
        creds["garmin_email"] = email
        creds["garmin_password"] = password
        
        with open(creds_path, 'w') as f:
            json.dump(creds, f)
            
        # Update running config
        import config
        config.GARMIN_EMAIL = email
        config.GARMIN_PASSWORD = password
        
        # Also need to update `sync_app`'s reference to it
        sync_app.GARMIN_EMAIL = email
        sync_app.GARMIN_PASSWORD = password
        
        # Also update local globals if used
        global GARMIN_EMAIL, GARMIN_PASSWORD
        GARMIN_EMAIL = email
        GARMIN_PASSWORD = password
        
        return jsonify({"message": "Garmin Credentials Saved!"})
    except Exception as e:
        return jsonify({"message": f"Error saving. Error type: {type(e).__name__}"}), 500

if __name__ == '__main__':
    print("Starting server on 0.0.0.0:5000", flush=True)
    app.run(host='0.0.0.0', port=5000)
