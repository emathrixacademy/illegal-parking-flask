import os
import psycopg2
import logging
import json

POSTGRES_URL = os.environ.get("DATABASE_URL") or "postgresql://postgres:osBsKkhgPnjxUCtzUDZFSLAVTEvuqCNH@postgres.railway.internal:5432/railway"

logger = logging.getLogger("db")

def get_connection():
    """Get a PostgreSQL connection."""
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL not set")
    return psycopg2.connect(POSTGRES_URL)

def table_exists(cur, table_name):
    """Check if a table exists in the database."""
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = %s
        );
    """, (table_name,))
    return cur.fetchone()[0]

def column_exists(cur, table_name, column_name):
    """Check if a column exists in a table."""
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = %s 
            AND column_name = %s
        );
    """, (table_name, column_name))
    return cur.fetchone()[0]

def ensure_tables():
    """Create violations and config tables if they don't exist."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Check and create violations table
        if not table_exists(cur, 'violations'):
            cur.execute("""
                CREATE TABLE violations (
                    id SERIAL PRIMARY KEY,
                    camera VARCHAR(32),
                    tracker_id INTEGER,
                    label VARCHAR(32),
                    timestamp TIMESTAMP,
                    image_path TEXT,
                    confidence_score REAL DEFAULT 0.0,
                    duration_minutes REAL DEFAULT 0.0,
                    fine_amount REAL DEFAULT 0.0,
                    barangay TEXT DEFAULT 'Bgry. Kanluran',
                    enforced BOOLEAN DEFAULT FALSE
                );
            """)
            logger.info("Created 'violations' table.")

        # Ensure performance indexes on violations
        cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_timestamp ON violations(timestamp);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_camera ON violations(camera);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_tracker ON violations(tracker_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_camera_tracker ON violations(camera, tracker_id);")

        # Check and create config table
        if not table_exists(cur, 'config'):
            cur.execute("""
                CREATE TABLE config (
                    key VARCHAR(64) PRIMARY KEY,
                    value TEXT
                );
            """)
            logger.info("Created 'config' table.")
        
        # Check and add updated_at column to config if it doesn't exist
        if table_exists(cur, 'config') and not column_exists(cur, 'config', 'updated_at'):
            try:
                cur.execute("""
                    ALTER TABLE config ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
                """)
                logger.info("Added 'updated_at' column to config table.")
            except Exception as e:
                logger.warning(f"Could not add updated_at column: {e}")

        # Check and add confidence_score, duration_minutes and barangay columns to violations if they don't exist
        if table_exists(cur, 'violations'):
            if not column_exists(cur, 'violations', 'confidence_score'):
                try:
                    cur.execute("""
                        ALTER TABLE violations ADD COLUMN confidence_score REAL DEFAULT 0.0;
                    """)
                    logger.info("Added 'confidence_score' column to violations table.")
                except Exception as e:
                    logger.warning(f"Could not add confidence_score column to violations: {e}")
            if not column_exists(cur, 'violations', 'duration_minutes'):
                try:
                    cur.execute("""
                        ALTER TABLE violations ADD COLUMN duration_minutes REAL DEFAULT 0.0;
                    """)
                    logger.info("Added 'duration_minutes' column to violations table.")
                except Exception as e:
                    logger.warning(f"Could not add duration_minutes column to violations: {e}")
            if not column_exists(cur, 'violations', 'fine_amount'):
                try:
                    cur.execute("""
                        ALTER TABLE violations ADD COLUMN fine_amount REAL DEFAULT 0.0;
                    """)
                    logger.info("Added 'fine_amount' column to violations table.")
                except Exception as e:
                    logger.warning(f"Could not add fine_amount column to violations: {e}")
            if not column_exists(cur, 'violations', 'enforced'):
                try:
                    cur.execute("""
                        ALTER TABLE violations ADD COLUMN enforced BOOLEAN DEFAULT FALSE;
                    """)
                    logger.info("Added 'enforced' column to violations table.")
                except Exception as e:
                    logger.warning(f"Could not add enforced column to violations: {e}")
            if not column_exists(cur, 'violations', 'barangay'):
                try:
                    cur.execute("""
                        ALTER TABLE violations ADD COLUMN barangay TEXT DEFAULT 'Bgry. Kanluran';
                    """)
                    logger.info("Added 'barangay' column to violations table.")
                except Exception as e:
                    logger.warning(f"Could not add barangay column to violations: {e}")

        # --- Feature 13: plate_records table ---
        if not table_exists(cur, 'plate_records'):
            cur.execute("""
                CREATE TABLE plate_records (
                    id SERIAL PRIMARY KEY,
                    violation_id INTEGER REFERENCES violations(id),
                    plate_number VARCHAR(20),
                    confidence REAL DEFAULT 0.0,
                    plate_image_path TEXT,
                    camera VARCHAR(32),
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_plate_number ON plate_records(plate_number);
                CREATE INDEX IF NOT EXISTS idx_plate_timestamp ON plate_records(timestamp);
            """)
            logger.info("Created 'plate_records' table.")

        # --- Feature 14: tamper_events table ---
        if not table_exists(cur, 'tamper_events'):
            cur.execute("""
                CREATE TABLE tamper_events (
                    id SERIAL PRIMARY KEY,
                    camera VARCHAR(32),
                    tamper_type VARCHAR(32),
                    details JSONB,
                    last_good_frame_path TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved BOOLEAN DEFAULT FALSE
                );
            """)
            logger.info("Created 'tamper_events' table.")

        # --- Feature 17: alert_log table ---
        if not table_exists(cur, 'alert_log'):
            cur.execute("""
                CREATE TABLE alert_log (
                    id SERIAL PRIMARY KEY,
                    alert_type VARCHAR(16),
                    camera VARCHAR(32),
                    vehicle_type VARCHAR(32),
                    success BOOLEAN,
                    error_message TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            logger.info("Created 'alert_log' table.")

        # --- Feature 18: users table ---
        if not table_exists(cur, 'users'):
            cur.execute("""
                CREATE TABLE users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(64) UNIQUE NOT NULL,
                    password_hash VARCHAR(256) NOT NULL,
                    role VARCHAR(16) NOT NULL DEFAULT 'viewer',
                    display_name VARCHAR(128),
                    email VARCHAR(128),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                );
            """)
            logger.info("Created 'users' table.")

            # Create default admin user (password: admin2026)
            try:
                import bcrypt
                default_pw = bcrypt.hashpw("admin2026".encode(), bcrypt.gensalt()).decode()
                cur.execute("""
                    INSERT INTO users (username, password_hash, role, display_name)
                    VALUES ('admin', %s, 'admin', 'System Administrator')
                    ON CONFLICT (username) DO NOTHING
                """, (default_pw,))
                logger.info("Created default admin user.")
            except Exception as e:
                logger.warning(f"Could not create default admin user: {e}")

        # --- Feature 18: activity_log table ---
        if not table_exists(cur, 'activity_log'):
            cur.execute("""
                CREATE TABLE activity_log (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    action VARCHAR(64),
                    details TEXT,
                    ip_address VARCHAR(45),
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            logger.info("Created 'activity_log' table.")

        conn.commit()
        cur.close()
        logger.info("Ensured all tables exist.")
    except Exception as e:
        logger.error(f"Failed to ensure tables: {e}")
    finally:
        if conn:
            conn.close()

def insert_violation_event(camera, tracker_id, label, timestamp, image_path, confidence_score=0.0, duration_minutes=0.0, fine_amount=0.0, barangay='Bgry. Kanluran', enforced=False):
    if not POSTGRES_URL:
        logging.warning("POSTGRES_URL not set, skipping DB insert.")
        return
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO violations (camera, tracker_id, label, timestamp, image_path, confidence_score, duration_minutes, fine_amount, barangay, enforced)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (camera, tracker_id, label, timestamp, image_path, confidence_score, duration_minutes, fine_amount, barangay, enforced))
        conn.commit()
        cur.close()
    except Exception as e:
        logging.error(f"Failed to insert violation event: {e}")
    finally:
        if conn:
            conn.close()

def get_config_value(key, default=None):
    """Get a single config value from the database."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Check if table exists first
        if not table_exists(cur, 'config'):
            cur.close()
            return default

        cur.execute("SELECT value FROM config WHERE key = %s", (key,))
        row = cur.fetchone()
        cur.close()
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
    finally:
        if conn:
            conn.close()

def set_config_value(key, value):
    """Set a single config value in the database (upsert)."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Ensure table exists
        if not table_exists(cur, 'config'):
            ensure_tables()

        if isinstance(value, (dict, list)):
            value_str = json.dumps(value)
        else:
            value_str = str(value)

        # Check if updated_at column exists
        has_updated_at = column_exists(cur, 'config', 'updated_at')

        if has_updated_at:
            cur.execute("""
                INSERT INTO config (key, value, updated_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
            """, (key, value_str))
        else:
            cur.execute("""
                INSERT INTO config (key, value)
                VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, (key, value_str))

        conn.commit()
        cur.close()
        logger.info(f"Config '{key}' updated in database.")
    except Exception as e:
        logger.error(f"Failed to set config '{key}': {e}")
    finally:
        if conn:
            conn.close()

def get_all_settings():
    """Get all settings from the database as a dictionary."""
    defaults = {
        "VIOLATION_TIME_THRESHOLD": 100,
        "REPEAT_CAPTURE_INTERVAL": 60,
        "PARKING_ZONES": {}
    }
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Check if table exists
        if not table_exists(cur, 'config'):
            cur.close()
            return defaults

        cur.execute("SELECT key, value FROM config")
        rows = cur.fetchall()
        cur.close()

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
    finally:
        if conn:
            conn.close()

def save_settings(settings_dict):
    """Save multiple settings to the database."""
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Ensure table exists
        if not table_exists(cur, 'config'):
            conn.close()
            conn = None
            ensure_tables()
            conn = get_connection()
            cur = conn.cursor()

        # Check if updated_at column exists
        has_updated_at = column_exists(cur, 'config', 'updated_at')

        for key, value in settings_dict.items():
            if isinstance(value, (dict, list)):
                value_str = json.dumps(value)
            else:
                value_str = str(value)

            if has_updated_at:
                cur.execute("""
                    INSERT INTO config (key, value, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
                """, (key, value_str))
            else:
                cur.execute("""
                    INSERT INTO config (key, value)
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (key, value_str))

        conn.commit()
        cur.close()
        logger.info(f"Saved {len(settings_dict)} settings to database.")
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
    finally:
        if conn:
            conn.close()

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
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Ensure table exists first
        if not table_exists(cur, 'config'):
            conn.close()
            conn = None
            ensure_tables()
            conn = get_connection()
            cur = conn.cursor()

        # Check if updated_at column exists
        has_updated_at = column_exists(cur, 'config', 'updated_at')

        for key, value in defaults.items():
            if isinstance(value, (dict, list)):
                value_str = json.dumps(value)
            else:
                value_str = str(value)

            # Only insert if key doesn't exist
            if has_updated_at:
                cur.execute("""
                    INSERT INTO config (key, value, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (key) DO NOTHING
                """, (key, value_str))
            else:
                cur.execute("""
                    INSERT INTO config (key, value)
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO NOTHING
                """, (key, value_str))

        conn.commit()
        cur.close()
        logger.info("Initialized default settings in database.")
    except Exception as e:
        logger.error(f"Failed to init default settings: {e}")
    finally:
        if conn:
            conn.close()
