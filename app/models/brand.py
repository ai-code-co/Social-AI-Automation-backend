from sqlalchemy import Column, ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from app.models.base import Base

class BrandSettings(Base):
    __tablename__ = "brand_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    company_name = Column(String(255), nullable=False)
    industry = Column(String(100), default="general")
    tone = Column(String(100), default="professional")
    target_audience = Column(Text)
    brand_voice = Column(Text)
    topics = Column(Text)
    hashtags = Column(Text)
    enabled_platforms = Column(String(255), default="instagram,facebook")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
