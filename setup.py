import os
import sys
import getpass

def prompt_for_input(prompt_text):
    """Helper to get input in a way that works in Docker -it"""
    try:
        return input(prompt_text).strip()
    except EOFError:
        print("\n[ERROR] Output handle closed. Are you running with 'docker-compose run -it ...'?")
        sys.exit(1)

def main():
    print("========================================")
    print("  Garmin Body Composition Import Setup  ")
    print("========================================")
    print("This script will configure your credentials and authenticate with Withings.")
    print("Your settings will be saved to .env and withings_tokens.pkl (mounted volumes).")
    print("")

    # 1. Gather Credentials
    client_id = prompt_for_input("Enter Withings Client ID: ")
    client_secret = prompt_for_input("Enter Withings Client Secret: ")
    garmin_email = prompt_for_input("Enter Garmin Email: ")
    try:
        garmin_password = getpass.getpass("Enter Garmin Password: ").strip()
    except EOFError:
         print("\n[ERROR] Output handle closed.")
         sys.exit(1)

    if not all([client_id, client_secret, garmin_email, garmin_password]):
        print("Error: All fields are required.")
        return

    # 2. Write to .env
    env_content = f"""WITHINGS_CLIENT_ID={client_id}
WITHINGS_CLIENT_SECRET={client_secret}
WITHINGS_REDIRECT_URI=http://localhost:8080
GARMIN_EMAIL={garmin_email}
GARMIN_PASSWORD={garmin_password}
"""
    with open(".env", "w") as f:
        f.write(env_content)
    
    print("\n[OK] .env file updated.")

    # 3. Update Current Environment (so imports work immediately)
    os.environ['WITHINGS_CLIENT_ID'] = client_id
    os.environ['WITHINGS_CLIENT_SECRET'] = client_secret
    os.environ['WITHINGS_REDIRECT_URI'] = 'http://localhost:8080'
    os.environ['GARMIN_EMAIL'] = garmin_email
    os.environ['GARMIN_PASSWORD'] = garmin_password

    # 4. Trigger Authentication
    # Import here so it picks up the new env vars (via config.py or os.getenv calls)
    try:
        # We need to forcefully reload config if it was already imported, but let's assume it wasn't.
        # Actually, if we use os.environ above, config.py using os.getenv() might have already run if included by top-level imports?
        # sync_app imports config. Let's check if we can patch config.
        
        import config
        config.WITHINGS_CLIENT_ID = client_id
        config.WITHINGS_CLIENT_SECRET = client_secret
        config.GARMIN_EMAIL = garmin_email
        config.GARMIN_PASSWORD = garmin_password
        
        import sync_app
        print("\n[INFO] Starting Withings Authentication...")
        sync_app.get_withings_credentials()
        print("\n[SUCCESS] Setup complete! You can now run 'docker-compose up -d'")
        
    except Exception as e:
        print(f"\n[ERROR] Setup failed. Error type: {type(e).__name__}")
        sys.exit(1)

if __name__ == "__main__":
    main()
