import os
from datetime import datetime, date
from flask import Flask, render_template, jsonify
from models import db, Violation, init_db
from sqlalchemy import func

# Initialize Flask app
app = Flask(__name__, static_folder="static", template_folder="templates")

# Get DATABASE_URL from environment
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL environment variable not set!")

# Fix Render's old postgres:// format
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    print("✅ Fixed DATABASE_URL format")

# Configure Flask app
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,  # Verify connections before using
    'pool_recycle': 300,    # Recycle connections after 5 minutes
}

print(f"🔗 Connecting to database...")

# Initialize database
try:
    init_db(app)
    print("✅ Database initialized successfully")
except Exception as e:
    print(f"❌ Database initialization failed: {e}")
    raise

# Health check route
@app.route("/health")
def health():
    """Health check endpoint for Render"""
    try:
        # Test database connection
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

@app.route("/")
def dashboard():
    """Main dashboard route"""
    try:
        # Get today's start time
        today_start = datetime.combine(date.today(), datetime.min.time())
        
        # Query violations
        total = db.session.query(func.count(Violation.id))\
            .filter(Violation.timestamp >= today_start)\
            .scalar() or 0
        
        active = db.session.query(func.count(Violation.id))\
            .filter(Violation.timestamp >= today_start, Violation.status == 'active')\
            .scalar() or 0
        
        resolved = total - active
        
        # Average duration
        avg_duration = db.session.query(func.avg(Violation.duration))\
            .filter(Violation.timestamp >= today_start)\
            .scalar() or 0
        avg_duration = float(avg_duration or 0)
        
        # Recent violations
        recent = db.session.query(Violation)\
            .order_by(Violation.timestamp.desc())\
            .limit(3)\
            .all()
        
        print(f"📊 Dashboard data: total={total}, active={active}, resolved={resolved}")
        
        return render_template(
            "dashboard.html",
            total=total,
            active=active,
            resolved=resolved,
            avg_duration=avg_duration,
            recent=recent
        )
    
    except Exception as e:
        print(f"❌ Dashboard error: {e}")
        return f"<h1>Error loading dashboard</h1><p>{str(e)}</p>", 500

# Error handlers
@app.errorhandler(500)
def internal_error(error):
    return f"<h1>500 Internal Server Error</h1><p>{str(error)}</p>", 500

@app.errorhandler(404)
def not_found(error):
    return "<h1>404 Not Found</h1>", 404

if __name__ == "__main__":
    # This is only for local development
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
