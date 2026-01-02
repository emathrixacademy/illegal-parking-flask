from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime
from db import Base

class Violation(Base):
    __tablename__ = "violations"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(String, nullable=False)
    plate_number = Column(String)
    confidence = Column(Float)
    image_path = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
