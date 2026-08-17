import asyncio
import io
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
import tzlocal

import config

logger = logging.getLogger("xiaomi_sync")

DATA_DIR = "data"
DB_PATH = os.path.join(DATA_DIR, "garmin_import.db")


def sanitize_hex(val: str) -> str:
    if not val:
        return ""
    return str(val).replace(":", "").replace(" ", "").replace("-", "").strip()


def append_history(status: str, log_output: str):
    """Appends a new entry to the sync history database."""
    try:
        now_local = datetime.now(tzlocal.get_localzone())
        timestamp = now_local.strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                "CREATE TABLE IF NOT EXISTS sync_history (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, status TEXT, log TEXT)"
            )
            c.execute(
                "INSERT INTO sync_history (timestamp, status, log) VALUES (?, ?, ?)",
                (timestamp, status, log_output),
            )
            conn.commit()
    except Exception as e:
        print(f"[XiaomiSync] Failed to append history: {e}", flush=True)


def upload_body_composition_to_garmin(weight: float, comp: dict, timestamp_dt: datetime = None) -> tuple[bool, str]:
    """Uploads body composition metrics to Garmin Connect."""
    garmin_email = config.GARMIN_EMAIL
    garmin_password = config.GARMIN_PASSWORD

    if not garmin_email or not garmin_password:
        return False, "Garmin email and password are not configured."

    try:
        from garminconnect import Garmin

        token_dir = os.path.join(DATA_DIR, ".garminconnect")
        os.makedirs(token_dir, exist_ok=True)

        garmin = Garmin(garmin_email, garmin_password)
        try:
            garmin.login(tokenstore=token_dir)
        except Exception:
            try:
                for f in os.listdir(token_dir):
                    fp = os.path.join(token_dir, f)
                    if os.path.isfile(fp):
                        os.unlink(fp)
            except Exception:
                pass
            garmin.login(tokenstore=token_dir)

        local_tz = tzlocal.get_localzone()
        if not timestamp_dt:
            timestamp_dt = datetime.now(timezone.utc)
        dt_local = timestamp_dt.astimezone(local_tz)
        timestamp_str = dt_local.isoformat()

        percent_fat = comp.get("fat_percent")
        percent_hydration = comp.get("water_percent")
        visceral_fat = comp.get("visceral_fat")
        bone_mass = comp.get("bone_mass_kg")
        muscle_mass = comp.get("muscle_mass_kg")
        bmi = comp.get("bmi")

        log_lines = [
            f"Uploading Xiaomi S400 Measurement to Garmin Connect ({garmin_email})...",
            f"Timestamp: {timestamp_str}",
            f"Weight: {weight:.2f} kg",
            f"BMI: {bmi}",
            f"Body Fat: {percent_fat}%",
            f"Muscle Mass: {muscle_mass} kg",
            f"Water / Hydration: {percent_hydration}%",
            f"Bone Mass: {bone_mass} kg",
            f"Visceral Fat: {visceral_fat}",
        ]
        log_str = "\n".join(log_lines)
        print(f"[XiaomiSync] {log_str}", flush=True)

        garmin.add_body_composition(
            timestamp=timestamp_str,
            weight=weight,
            percent_fat=percent_fat,
            percent_hydration=percent_hydration,
            visceral_fat_rating=visceral_fat,
            bone_mass=bone_mass,
            muscle_mass=muscle_mass,
            bmi=bmi,
        )
        return True, log_str + "\nSuccessfully uploaded to Garmin Connect!"
    except Exception as e:
        err_msg = f"Failed to upload to Garmin: {type(e).__name__}: {e}"
        print(f"[XiaomiSync] {err_msg}", flush=True)
        return False, err_msg


class XiaomiScaleWorker:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.state = "idle"  # idle, listening, connected, syncing, disabled, error
        self.message = "Initializing..."
        self.last_sync = None
        self.last_weight = None
        self.last_error = None

    def get_status(self) -> dict:
        config.reload_config()
        configured = bool(config.XIAOMI_MAC and config.XIAOMI_BINDKEY and config.XIAOMI_TOKEN)
        return {
            "configured": configured,
            "enabled": bool(config.XIAOMI_ENABLED),
            "state": self.state if config.XIAOMI_ENABLED else "disabled",
            "message": self.message if config.XIAOMI_ENABLED else "Xiaomi scale syncing is disabled.",
            "mac": config.XIAOMI_MAC or "",
            "last_sync": self.last_sync,
            "last_weight": self.last_weight,
            "last_error": self.last_error,
            "target_weight": config.XIAOMI_TARGET_WEIGHT,
            "weight_tolerance": config.XIAOMI_WEIGHT_TOLERANCE,
            "profile": {
                "sex": config.XIAOMI_SEX,
                "age": config.XIAOMI_AGE,
                "height": config.XIAOMI_HEIGHT,
            },
        }

    def start(self):
        config.reload_config()
        if not config.XIAOMI_ENABLED:
            self.state = "disabled"
            self.message = "Xiaomi scale syncing is disabled."
            return

        if not (config.XIAOMI_MAC and config.XIAOMI_BINDKEY and config.XIAOMI_TOKEN):
            self.state = "error"
            self.message = "Missing MAC address, BindKey, or Token."
            return

        if self._thread and self._thread.is_alive():
            print("[XiaomiSync] Worker is already running.", flush=True)
            return

        self._stop_event.clear()
        self.state = "listening"
        self.message = f"Scanning for Xiaomi Scale ({config.XIAOMI_MAC})..."
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True, name="XiaomiScaleWorker")
        self._thread.start()
        print(f"[XiaomiSync] Worker started for scale {config.XIAOMI_MAC}", flush=True)

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self.state = "idle"
        self.message = "Worker stopped."
        print("[XiaomiSync] Worker stopped.", flush=True)

    def restart(self):
        self.stop()
        self.start()

    def _run_async_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._listen_loop())
        finally:
            loop.close()

    async def _listen_loop(self):
        try:
            import xiaomi_s400_live as xscale
            from xiaomi_s400_live import S400Scale, UserProfile
        except ImportError as e:
            self.state = "error"
            self.last_error = f"Dependencies missing: {e}"
            self.message = "xiaomi-s400-live or bleak is not installed."
            print(f"[XiaomiSync] {self.message}", flush=True)
            return

        while not self._stop_event.is_set():
            config.reload_config()
            mac = config.XIAOMI_MAC
            bindkey_hex = sanitize_hex(config.XIAOMI_BINDKEY)
            token_hex = sanitize_hex(config.XIAOMI_TOKEN)

            if not mac or not bindkey_hex or not token_hex:
                self.state = "error"
                self.message = "Incomplete credentials."
                await asyncio.sleep(5.0)
                continue

            try:
                bindkey_bytes = bytes.fromhex(bindkey_hex)
                token_bytes = bytes.fromhex(token_hex)
            except Exception as e:
                self.state = "error"
                self.message = f"Invalid hex in keys: {e}"
                await asyncio.sleep(5.0)
                continue

            profile = UserProfile(
                sex=config.XIAOMI_SEX or "male",
                age_years=int(config.XIAOMI_AGE or 30),
                height_cm=int(config.XIAOMI_HEIGHT or 180),
            )

            self.state = "listening"
            self.message = f"Listening for weigh-in on {mac}..."

            try:
                async with S400Scale(
                    mac=mac,
                    bindkey=bindkey_bytes,
                    token=token_bytes,
                    profile=profile,
                    connect_timeout=25.0,
                ) as scale:
                    self.state = "connected"
                    self.message = "Connected to Xiaomi S400! Measuring..."
                    print(f"[XiaomiSync] Connected to {mac}", flush=True)

                    async for event in scale.events():
                        if self._stop_event.is_set():
                            break

                        if event.type == "live":
                            status_text = "Stabilized (measuring impedance...)" if event.stable else "Measuring weight..."
                            self.message = f"Live: {event.weight_kg:.2f} kg ({status_text})"

                        elif event.type == "final":
                            self.state = "syncing"
                            self.message = f"Processing measurement: {event.weight_kg:.2f} kg"
                            weight = event.weight_kg
                            ts = (
                                datetime.fromtimestamp(event.timestamp, timezone.utc)
                                if event.timestamp
                                else datetime.now(timezone.utc)
                            )
                            comp = event.body_composition or {}

                            # Multi-User weight filtering
                            target_weight = config.XIAOMI_TARGET_WEIGHT
                            tolerance = config.XIAOMI_WEIGHT_TOLERANCE or 5.0
                            is_match = True

                            if target_weight is not None:
                                min_w = target_weight - tolerance
                                max_w = target_weight + tolerance
                                if not (min_w <= weight <= max_w):
                                    is_match = False
                                    log_out = (
                                        f"Xiaomi Scale S400 Measurement: {weight:.2f} kg at {ts.isoformat()}\n"
                                        f"Filter Rejection: Weight {weight:.2f} kg is outside target window ({min_w:.1f} - {max_w:.1f} kg).\n"
                                        f"Skipped uploading to Garmin Connect to avoid syncing another user."
                                    )
                                    print(f"[XiaomiSync] {log_out}", flush=True)
                                    append_history("Xiaomi Scale (Filtered)", log_out)
                                    self.message = f"Measurement {weight:.2f} kg filtered out (not matching target profile)."
                                    break

                            if is_match:
                                self.last_weight = f"{weight:.2f} kg"
                                self.last_sync = datetime.now(tzlocal.get_localzone()).strftime("%Y-%m-%d %H:%M:%S")

                                # Upload to Garmin Connect
                                success, upload_log = upload_body_composition_to_garmin(weight, comp, ts)
                                status_str = "Xiaomi Scale (Success)" if success else "Xiaomi Scale (Upload Failed)"
                                full_log = (
                                    f"Xiaomi Scale S400 Measurement:\n"
                                    f"Weight: {weight:.2f} kg\n"
                                    f"Body Composition: {json.dumps(comp, indent=2)}\n\n"
                                    f"Garmin Sync Result:\n{upload_log}"
                                )
                                append_history(status_str, full_log)
                                if success:
                                    self.message = f"Successfully synced {weight:.2f} kg to Garmin!"
                                else:
                                    self.last_error = upload_log
                                    self.message = f"Sync failed: {upload_log}"
                            break

            except asyncio.CancelledError:
                break
            except Exception as e:
                err_str = str(e)
                if "not found" not in err_str.lower() and "timeout" not in err_str.lower():
                    self.last_error = err_str
                    print(f"[XiaomiSync] Scale connection event: {type(e).__name__}: {e}", flush=True)
                await asyncio.sleep(2.0)


XIAOMI_WORKER = XiaomiScaleWorker()
