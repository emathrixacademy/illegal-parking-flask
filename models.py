from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Violation(db.Model):
    __tablename__ = 'violations'
    
    id = db.Column(db.Integer, primary_key=True)
    plate_number = db.Column(db.String(64))
    vehicle_type = db.Column(db.String(64))
    location = db.Column(db.String(128))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    duration = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(32), default='active')
    image_url = db.Column(db.String(512))

def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
