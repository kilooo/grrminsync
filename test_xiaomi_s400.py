import asyncio
import json
import os
import sys
from datetime import datetime, timezone
import tzlocal

import xiaomi_s400_live as xscale
from xiaomi_s400_live import S400Scale, UserProfile

# Ensure UTF-8 output in Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

CONFIG_FILE = "xiaomi_scale_config.json"



def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found!")
        print(f"Please copy xiaomi_scale_config.example.json to {CONFIG_FILE} and fill in your details.")
        sys.exit(1)

    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def sanitize_hex(val: str) -> str:
    """Removes colons, spaces, and ensures lowercase hex."""
    return val.replace(":", "").replace(" ", "").replace("-", "").strip()


def validate_config(cfg: dict):
    scale_cfg = cfg.get("scale", {})
    mac = scale_cfg.get("mac_address", "").strip()
    bindkey_hex = sanitize_hex(scale_cfg.get("bindkey", ""))
    token_hex = sanitize_hex(scale_cfg.get("token", ""))

    if not mac or "XX" in mac.upper():
        print("❌ Error: Please fill in a valid scale 'mac_address' in xiaomi_scale_config.json")
        sys.exit(1)

    if not bindkey_hex or "YOUR" in bindkey_hex.upper():
        print("❌ Error: Please fill in a valid 32-character 'bindkey' in xiaomi_scale_config.json")
        sys.exit(1)

    if not token_hex or "YOUR" in token_hex.upper():
        print("❌ Error: Please fill in a valid 24-character 'token' in xiaomi_scale_config.json")
        sys.exit(1)

    try:
        bindkey_bytes = bytes.fromhex(bindkey_hex)
        token_bytes = bytes.fromhex(token_hex)
    except ValueError as e:
        print(f"❌ Error: bindkey or token is not valid hexadecimal: {e}")
        sys.exit(1)

    profile_cfg = cfg.get("user_profile", {})
    sex = profile_cfg.get("sex", "male").lower()
    if sex not in ("male", "female"):
        print("❌ Error: sex must be 'male' or 'female'")
        sys.exit(1)

    profile = UserProfile(
        sex=sex,
        age_years=int(profile_cfg.get("age_years", 30)),
        height_cm=int(profile_cfg.get("height_cm", 180)),
    )

    return mac, bindkey_bytes, token_bytes, profile, profile_cfg, cfg.get("garmin", {})


def sync_garmin_measurement(garmin_cfg: dict, weight: float, comp: dict, timestamp_dt: datetime):
    email = garmin_cfg.get("email")
    password = garmin_cfg.get("password")

    # If not in config file, check config.py / credentials.json
    if not email or not password:
        try:
            import config
            email = email or config.GARMIN_EMAIL
            password = password or config.GARMIN_PASSWORD
        except Exception:
            pass

    if not email or not password:
        print("⚠️ Garmin sync skipped: Email or Password missing.")
        return

    try:
        from garminconnect import Garmin
        print(f"\n🚀 Authenticating with Garmin Connect ({email})...")
        garmin_client = Garmin(email, password)
        garmin_client.login()
        print("✅ Logged into Garmin Connect successfully.")

        local_tz = tzlocal.get_localzone()
        dt_local = timestamp_dt.astimezone(local_tz)
        timestamp_str = dt_local.isoformat()

        percent_fat = comp.get("fat_percent")
        percent_hydration = comp.get("water_percent")
        visceral_fat = comp.get("visceral_fat")
        bone_mass = comp.get("bone_mass_kg")
        muscle_mass = comp.get("muscle_mass_kg")
        bmi = comp.get("bmi")

        print(f"📤 Uploading Body Composition to Garmin Connect...")
        print(f"   Timestamp: {timestamp_str}")
        print(f"   Weight: {weight:.2f} kg | Fat: {percent_fat}% | Muscle: {muscle_mass} kg | Water: {percent_hydration}%")

        garmin_client.add_body_composition(
            timestamp=timestamp_str,
            weight=weight,
            percent_fat=percent_fat,
            percent_hydration=percent_hydration,
            visceral_fat_rating=visceral_fat,
            bone_mass=bone_mass,
            muscle_mass=muscle_mass,
            bmi=bmi,
        )
        print("🎉 Successfully uploaded measurement to Garmin Connect!")
    except Exception as e:
        print(f"❌ Failed to upload to Garmin Connect: {type(e).__name__}: {e}")


async def listen_for_scale(mac: str, bindkey: bytes, token: bytes, profile: UserProfile, profile_cfg: dict, garmin_cfg: dict):
    target_weight = profile_cfg.get("target_weight_kg")
    tolerance = profile_cfg.get("weight_tolerance_kg", 5.0)

    print("\n" + "=" * 60)
    print(" ⚖️  Xiaomi Body Composition Scale S400 BLE Listener")
    print("=" * 60)
    print(f" Target MAC Address : {mac}")
    print(f" Profile            : {profile.sex.capitalize()}, {profile.age_years} yrs, {profile.height_cm} cm")
    if target_weight:
        min_w = target_weight - tolerance
        max_w = target_weight + tolerance
        print(f" Multi-User Filter  : {target_weight:.1f} kg ± {tolerance:.1f} kg (Window: {min_w:.1f} kg - {max_w:.1f} kg)")
    else:
        print(" Multi-User Filter  : Disabled (Will accept all measurements)")
    print(f" Garmin Sync Enabled: {garmin_cfg.get('sync_to_garmin', False)}")
    print("=" * 60)
    print("\n🔍 Scanning for scale over Bluetooth...")
    print("👉 Please STEP ON THE SCALE to wake it up and begin measurement!\n")

    while True:
        try:
            async with S400Scale(
                mac=mac,
                bindkey=bindkey,
                token=token,
                profile=profile,
                connect_timeout=25.0
            ) as scale:
                print("🔗 Connected and authenticated with Xiaomi S400!")
                print("⏳ Standing on scale... Keep still for body composition measurement...")

                async for event in scale.events():
                    if event.type == "live":
                        status = "STABLE (measuring impedance...)" if event.stable else "measuring..."
                        sys.stdout.write(f"\r⚖️  Live Weight: {event.weight_kg:.2f} kg [{status}]   ")
                        sys.stdout.flush()

                    elif event.type == "final":
                        print(f"\n\n{'=' * 60}")
                        print("  🎉 MEASUREMENT COMPLETED!")
                        print("=" * 60)
                        weight = event.weight_kg
                        ts = datetime.fromtimestamp(event.timestamp, timezone.utc) if event.timestamp else datetime.now(timezone.utc)
                        print(f" Timestamp     : {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                        print(f" Weight        : {weight:.2f} kg")
                        if event.impedance_ohm:
                            print(f" Impedance     : {event.impedance_ohm:.1f} Ω (Low: {event.impedance_low_ohm or 0:.1f} Ω)")

                        comp = event.body_composition or {}
                        if comp:
                            print("-" * 60)
                            print(f" BMI           : {comp.get('bmi', 'N/A')}")
                            print(f" Body Fat %    : {comp.get('fat_percent', 'N/A')}%")
                            print(f" Muscle Mass   : {comp.get('muscle_mass_kg', 'N/A')} kg")
                            print(f" Water / Hydr. : {comp.get('water_percent', 'N/A')}%")
                            print(f" Bone Mass     : {comp.get('bone_mass_kg', 'N/A')} kg")
                            print(f" Visceral Fat  : {comp.get('visceral_fat', 'N/A')}")
                            print(f" BMR           : {comp.get('bmr_kcal_day', 'N/A')} kcal/day")
                            print(f" Metabolic Age : {comp.get('metabolic_age_years', 'N/A')} yrs")
                            print(f" Body Type     : {comp.get('body_type_name', 'N/A')}")
                        print("=" * 60)

                        # Multi-User weight check
                        is_my_measurement = True
                        if target_weight is not None:
                            min_w = target_weight - tolerance
                            max_w = target_weight + tolerance
                            if min_w <= weight <= max_w:
                                print(f"✅ Filter Check: Weight {weight:.2f} kg matches your target window ({min_w:.1f} - {max_w:.1f} kg).")
                            else:
                                is_my_measurement = False
                                print(f"🚫 Filter Check: Weight {weight:.2f} kg is OUTSIDE your target window ({min_w:.1f} - {max_w:.1f} kg).")
                                print("   Skipping sync to protect against other users' measurements.")

                        # Sync to Garmin if enabled and filter passed
                        if is_my_measurement and garmin_cfg.get("sync_to_garmin", False):
                            sync_garmin_measurement(garmin_cfg, weight, comp, ts)
                        elif is_my_measurement and not garmin_cfg.get("sync_to_garmin", False):
                            print("\n💡 Tip: Set 'sync_to_garmin': true in xiaomi_scale_config.json to auto-upload to Garmin.")

                        print("\n👉 Done! Waiting for next measurement (Press Ctrl+C to exit)...")
                        break  # Break inner loop and reconnect on next weigh-in

        except asyncio.CancelledError:
            print("\n👋 Exiting...")
            break
        except Exception as e:
            # Scale went to sleep or was not in range
            err_msg = str(e)
            if "not found" in err_msg.lower() or "timeout" in err_msg.lower():
                sys.stdout.write("\r⏳ Waiting for scale broadcast (Step on scale to wake)...      ")
                sys.stdout.flush()
            else:
                print(f"\n⚠️ Reconnecting: {type(e).__name__}: {e}")
            await asyncio.sleep(2.0)


def main():
    cfg = load_config()
    mac, bindkey, token, profile, profile_cfg, garmin_cfg = validate_config(cfg)

    try:
        asyncio.run(listen_for_scale(mac, bindkey, token, profile, profile_cfg, garmin_cfg))
    except KeyboardInterrupt:
        print("\n👋 Stopped by user.")


if __name__ == "__main__":
    main()
