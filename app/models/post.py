from sqlalchemy import Column, ForeignKey, Integer, String, Text, DateTime, Enum
from sqlalchemy.sql import func
import enum
from app.models.base import Base

class PostStatus(str, enum.Enum):
    draft = "draft"
    pending_approval = "pending_approval"
    approved = "approved"
    scheduled = "scheduled"
    published = "published"
    failed = "failed"
    paused = "paused"

class Platform(str, enum.Enum):
    facebook = "facebook"
    instagram = "instagram"
    linkedin = "linkedin"
    twitter = "twitter"
    tiktok = "tiktok"
    youtube = "youtube"

class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    brand_id = Column(Integer, ForeignKey("brand_settings.id"), nullable=True, index=True)
    platform = Column(Enum(Platform), nullable=False)
    caption = Column(Text, nullable=False)
    hashtags = Column(Text)
    image_prompt = Column(Text)       # prompt used to generate visual
    image_url = Column(String(500))   # final image URL or path
    status = Column(Enum(PostStatus), default=PostStatus.draft)
    status_before_pause = Column(String(50), nullable=True)
    scheduled_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    publish_attempts = Column(Integer, default=0)
    external_post_id = Column(String(255), nullable=True)
    platform_post_url = Column(String(500), nullable=True)
    error_log = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
