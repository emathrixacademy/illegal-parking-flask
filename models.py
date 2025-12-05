from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()

class Violation(db.Model):
    __tablename__ = 'violations'
    id = db.Column(db.Integer, primary_key=True)
    plate_number = db.Column(db.String(64), nullable=True)
    vehicle_type = db.Column(db.String(64), nullable=True)
    location = db.Column(db.String(128), nullable=True)
    image_url = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), nullable=False, default='active')  # active/resolved
    duration = db.Column(db.Float, nullable=True, default=0.0)  # minutes
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<Violation {self.id} {self.plate_number}>"
