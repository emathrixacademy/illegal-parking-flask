import os
import psycopg2
import logging

POSTGRES_URL = "postgresql://postgres:ltymHUMvXphOojaHeJRJGnyQUfWsghwq@mainline.proxy.rlwy.net:42362/railway"

def insert_violation_event(camera, tracker_id, label, timestamp, image_path):
    if not POSTGRES_URL:
        logging.warning("POSTGRES_URL not set, skipping DB insert.")
        return
    try:
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
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
        cur.execute("""
            INSERT INTO violations (camera, tracker_id, label, timestamp, image_path)
            VALUES (%s, %s, %s, %s, %s)
        """, (camera, tracker_id, label, timestamp, image_path))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Failed to insert violation event: {e}")
