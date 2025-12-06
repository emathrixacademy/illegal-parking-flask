import os
from datetime import datetime, date
from flask import Flask, render_template, url_for
from models import db, Violation, init_db
from sqlalchemy import func
from dotenv import load_dotenv

# Load .env in local development
load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set. See README.")

# 🔥 Fix Render's old-style Postgres URLs
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

init_db(app)  # bind SQLAlchemy

@app.route("/")
def dashboard():
    # today midnight
    today_start = datetime.combine(date.today(), datetime.min.time())

    # Total violations today
    total = db.session.query(func.count(Violation.id)).filter(Violation.timestamp >= today_start).scalar() or 0

    # Active now
    active = db.session.query(func.count(Violation.id)).filter(Violation.timestamp >= today_start, Violation.status == 'active').scalar() or 0

    resolved = total - active
    # average duration (minutes)
    avg_duration = db.session.query(func.avg(Violation.duration)).filter(Violation.timestamp >= today_start).scalar() or 0
    avg_duration = float(avg_duration or 0)

    # Recent violations (most recent 3)
    recent = db.session.query(Violation).order_by(Violation.timestamp.desc()).limit(3).all()

    # send to template
    return render_template(
        "dashboard.html",
        total=total,
        active=active,
        resolved=resolved,
        avg_duration=avg_duration,
        recent=recent
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
