import os
import psycopg2
import logging
import json

POSTGRES_URL = "postgresql://postgres:ltymHUMvXphOojaHeJRJGnyQUfWsghwq@mainline.proxy.rlwy.net:42362/railway"

logger = logging.getLogger("db")

def get_connection():
    """Get a PostgreSQL connection."""
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL not set")
    return psycopg2.connect(POSTGRES_URL)

def ensure_tables():
    """Create violations and config tables if they don't exist."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        # Violations table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS violations (
                id SERIAL PRIMARY KEY,
                camera VARCHAR(32),
                tracker_id INTEGER,
                label VARCHAR(32),
                timestamp TIMESTAMP,
                image_path TEXT
            );
        """)
        # Config table - stores key-value pairs for settings
        cur.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key VARCHAR(64) PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Ensured 'violations' and 'config' tables exist.")
    except Exception as e:
        logger.error(f"Failed to ensure tables: {e}")

def insert_violation_event(camera, tracker_id, label, timestamp, image_path):
    if not POSTGRES_URL:
        logging.warning("POSTGRES_URL not set, skipping DB insert.")
        return
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO violations (camera, tracker_id, label, timestamp, image_path)
            VALUES (%s, %s, %s, %s, %s)
        """, (camera, tracker_id, label, timestamp, image_path))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Failed to insert violation event: {e}")

def get_config_value(key, default=None):
    """Get a single config value from the database."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT value FROM config WHERE key = %s", (key,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            # Try to parse as JSON
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                return row[0]
        return default
    except Exception as e:
        logger.error(f"Failed to get config '{key}': {e}")
        return default

def set_config_value(key, value):
    """Set a single config value in the database (upsert)."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        # Convert to JSON string if dict/list
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value)
        else:
            value_str = str(value)
        cur.execute("""
            INSERT INTO config (key, value, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
        """, (key, value_str))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Config '{key}' updated in database.")
    except Exception as e:
        logger.error(f"Failed to set config '{key}': {e}")

def get_all_settings():
    """Get all settings from the database as a dictionary."""
    defaults = {
        "VIOLATION_TIME_THRESHOLD": 100,
        "REPEAT_CAPTURE_INTERVAL": 60,
        "PARKING_ZONES": {}
    }
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM config")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        settings = defaults.copy()
        for key, value in rows:
            try:
                settings[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                # Try int/float
                try:
                    if '.' in str(value):
                        settings[key] = float(value)
                    else:
                        settings[key] = int(value)
                except (ValueError, TypeError):
                    settings[key] = value
        return settings
    except Exception as e:
        logger.error(f"Failed to get all settings: {e}")
        return defaults

def save_settings(settings_dict):
    """Save multiple settings to the database."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        for key, value in settings_dict.items():
            if isinstance(value, (dict, list)):
                value_str = json.dumps(value)
            else:
                value_str = str(value)
            cur.execute("""
                INSERT INTO config (key, value, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
            """, (key, value_str))
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Saved {len(settings_dict)} settings to database.")
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")

def init_default_settings():
    """Initialize default settings in database if they don't exist."""
    defaults = {
        "VIOLATION_TIME_THRESHOLD": 100,
        "REPEAT_CAPTURE_INTERVAL": 60,
        "PARKING_ZONES": {
            "Camera_1": [[249, 242], [255, 404], [654, 426], [443, 261]],
            "Camera_2": [[46, 437], [453, 253], [664, 259], [678, 438]]
        }
    }
    try:
        conn = get_connection()
        cur = conn.cursor()
        for key, value in defaults.items():
            if isinstance(value, (dict, list)):
                value_str = json.dumps(value)
            else:
                value_str = str(value)
            # Only insert if key doesn't exist
            cur.execute("""
                INSERT INTO config (key, value, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO NOTHING
            """, (key, value_str))
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Initialized default settings in database.")
    except Exception as e:
        logger.error(f"Failed to init default settings: {e}")
