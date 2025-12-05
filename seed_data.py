from models import db, Violation
from app import app
from datetime import datetime, timedelta

with app.app_context():
    v1 = Violation(plate_number="ABC123", vehicle_type="Car", location="Brgy. Tagapo", status="active", duration=6.0, timestamp=datetime.utcnow())
    v2 = Violation(plate_number="XYZ789", vehicle_type="SUV", location="Brgy. Tagapo", status="resolved", duration=10.5, timestamp=datetime.utcnow() - timedelta(hours=1))
    db.session.add_all([v1, v2])
    db.session.commit()
    print("Seeded sample violations")
