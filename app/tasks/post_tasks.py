import logging
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.base import SessionLocal
from app.models.post import Post, PostStatus, Platform
from app.models.brand import BrandSettings
from app.services.image_service import get_post_image_url
from app.services.openai_service import generate_post

logger = logging.getLogger(__name__)

DEFAULT_TOPICS = [
    "new offer or service highlight",
    "educational tip for customers",
    "behind the scenes of the business",
    "customer success story",
    "industry trend commentary",
    "frequently asked question",
    "team or founder story",
    "community update",
]

POSTING_SCHEDULE = {
    Platform.instagram: {"days": ["mon", "wed", "fri"], "hour": 17},
    Platform.facebook: {"days": ["tue", "thu", "sat"], "hour": 17},
    Platform.linkedin: {"days": ["mon", "wed"], "hour": 17},
    Platform.twitter: {"days": ["mon", "tue", "wed", "thu", "fri"], "hour": 17},
}


def get_enabled_platforms(brand: BrandSettings) -> set[str]:
    platforms = {
        platform.strip().lower()
        for platform in (brand.enabled_platforms or "").split(",")
        if platform.strip()
    }
    return platforms or {"instagram", "facebook"}


def auto_generate_posts(brand_id: int | None = None, user_id: int | None = None):
    db: Session = SessionLocal()
    try:
        brands_query = db.query(BrandSettings)
        if user_id is not None:
            brands_query = brands_query.filter(BrandSettings.user_id == user_id)
        if brand_id is not None:
            brands_query = brands_query.filter(BrandSettings.id == brand_id)
        brands = brands_query.all()

        if not brands:
            logger.warning("No businesses found for auto generation")
            return 0

        today = datetime.now()
        day_name = today.strftime("%a").lower()
        posts_created = 0

        for brand in brands:
            brand_voice = brand.brand_voice or "clear, trustworthy, and engaging"
            hashtags = brand.hashtags or "#Business #SocialMedia #Marketing"
            topics = [
                topic.strip()
                for topic in (brand.topics or "").split(",")
                if topic.strip()
            ] or DEFAULT_TOPICS
            enabled_platforms = get_enabled_platforms(brand)

            for platform, schedule in POSTING_SCHEDULE.items():
                if platform.value not in enabled_platforms:
                    continue
                if day_name not in schedule["days"]:
                    continue

                topic_index = today.timetuple().tm_yday % len(topics)
                topic = topics[topic_index]

                logger.info(f"Generating post for {brand.company_name} on {platform.value}: {topic}")
                content = generate_post(
                    platform=platform.value,
                    topic=topic,
                    brand_voice=brand_voice,
                    hashtags=hashtags,
                    business_name=brand.company_name,
                    industry=brand.industry,
                    target_audience=brand.target_audience,
                )

                scheduled_time = today.replace(
                    hour=schedule["hour"],
                    minute=0,
                    second=0,
                    microsecond=0,
                )

                post = Post(
                    brand_id=brand.id,
                    platform=platform,
                    caption=content["caption"],
                    hashtags=content["hashtags"],
                    image_prompt=content["image_prompt"],
                    status=PostStatus.pending_approval,
                    scheduled_at=scheduled_time,
                )
                db.add(post)
                db.flush()
                post.image_url = get_post_image_url(post.id)
                posts_created += 1

        db.commit()
        logger.info(f"Auto-generated {posts_created} posts for today")
        return posts_created

    except Exception as e:
        logger.error(f"Error in auto_generate_posts: {e}")
        db.rollback()
        raise
    finally:
        db.close()
