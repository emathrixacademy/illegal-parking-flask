import bcrypt
import logging
from flask import session, request, redirect, url_for, jsonify
from functools import wraps
from db import get_connection, return_connection

logger = logging.getLogger("Auth")

ROLES = {
    'admin': 3,     # Full control
    'operator': 2,  # Monitor + actions (enforce, settings)
    'viewer': 1,    # Read-only dashboard access
}


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())


def authenticate(username, password):
    """Verify credentials. Returns user dict or None."""
    try:
        conn = get_connection()
    except Exception as e:
        logger.error(f"Database connection failed during authentication: {e}")
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, password_hash, role, display_name, is_active FROM users WHERE username = %s",
            (username,)
        )
        row = cur.fetchone()
        cur.close()
    finally:
        return_connection(conn)

    if not row:
        return None
    user_id, uname, pw_hash, role, display_name, is_active = row
    if not is_active:
        return None
    if not check_password(password, pw_hash):
        return None

    # Update last login
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = %s", (user_id,))
            conn.commit()
            cur.close()
        finally:
            return_connection(conn)
    except Exception as e:
        logger.error(f"Failed to update last_login: {e}")

    return {
        "id": user_id,
        "username": uname,
        "role": role,
        "display_name": display_name,
    }


def log_activity(user_id, action, details=None):
    """Log user activity."""
    try:
        ip = request.remote_addr if request else None
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO activity_log (user_id, action, details, ip_address)
            VALUES (%s, %s, %s, %s)
        """, (user_id, action, details, ip))
        conn.commit()
        cur.close()
        return_connection(conn)
    except Exception as e:
        logger.error(f"Failed to log activity: {e}")


def login_required(f):
    """Decorator: requires any logged-in user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            if request.path.startswith('/api/'):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


def role_required(min_role):
    """Decorator: requires minimum role level."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user' not in session:
                if request.path.startswith('/api/'):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect(url_for('login_page'))
            user_role = session['user'].get('role', 'viewer')
            if ROLES.get(user_role, 0) < ROLES.get(min_role, 0):
                if request.path.startswith('/api/'):
                    return jsonify({"error": "Insufficient permissions"}), 403
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated
    return decorator


# --- CRUD functions for user management ---

def list_users():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, role, display_name, email, is_active, created_at, last_login "
            "FROM users ORDER BY id"
        )
        rows = cur.fetchall()
        cols = ['id', 'username', 'role', 'display_name', 'email', 'is_active', 'created_at', 'last_login']
        cur.close()
        return [dict(zip(cols, r)) for r in rows]
    finally:
        return_connection(conn)


def create_user(username, password, role='viewer', display_name=None, email=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Check if username already exists
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cur.fetchone():
            cur.close()
            raise ValueError(f"Username '{username}' already exists")
        pw_hash = hash_password(password)
        cur.execute("""
            INSERT INTO users (username, password_hash, role, display_name, email)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (username, pw_hash, role, display_name, email))
        user_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return user_id
    finally:
        return_connection(conn)


def update_user(user_id, data):
    conn = get_connection()
    try:
        cur = conn.cursor()
        fields = []
        values = []
        for key in ['role', 'display_name', 'email', 'is_active']:
            if key in data:
                fields.append(f"{key} = %s")
                values.append(data[key])
        if 'password' in data and data['password']:
            fields.append("password_hash = %s")
            values.append(hash_password(data['password']))
        if fields:
            values.append(user_id)
            cur.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = %s", values)
            conn.commit()
        cur.close()
    finally:
        return_connection(conn)


def delete_user(user_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE activity_log SET user_id = NULL WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM users WHERE id = %s AND username != 'admin'", (user_id,))
        conn.commit()
        cur.close()
    finally:
        return_connection(conn)
