import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.base import SessionLocal
from app.models.post import Post, PostStatus
from app.models.social_account import SocialAccount
from app.services.linkedin_publisher import PublishError as LinkedInPublishError, publish_to_linkedin
from app.services.meta_publisher import PublishError as MetaPublishError, publish_to_meta
from app.services.x_publisher import PublishError as XPublishError, publish_to_x


logger = logging.getLogger(__name__)


def publish_due_posts() -> int:
    db: Session = SessionLocal()
    published_count = 0

    try:
        due_posts = (
            db.query(Post)
            .filter(Post.status == PostStatus.scheduled)
            .filter(Post.scheduled_at <= datetime.now())
            .order_by(Post.scheduled_at.asc())
            .all()
        )

        for post in due_posts:
            account = (
                db.query(SocialAccount)
                .filter(SocialAccount.brand_id == post.brand_id)
                .filter(SocialAccount.platform == post.platform.value)
                .filter(SocialAccount.is_active.is_(True))
                .first()
            )

            post.publish_attempts = (post.publish_attempts or 0) + 1

            if not account:
                post.status = PostStatus.failed
                post.error_log = f"No connected {post.platform.value} account found for this business."
                continue

            try:
                if post.platform.value in {"facebook", "instagram"}:
                    result = publish_to_meta(post, account)
                elif post.platform.value == "linkedin":
                    result = publish_to_linkedin(post, account)
                elif post.platform.value == "twitter":
                    result = publish_to_x(post, account)
                else:
                    raise MetaPublishError(f"Publishing is not configured for {post.platform.value} yet.")
                post.status = PostStatus.published
                post.published_at = datetime.now()
                post.external_post_id = result.get("external_post_id")
                post.platform_post_url = result.get("platform_post_url")
                post.error_log = None
                account.last_error = None
                published_count += 1
            except (MetaPublishError, LinkedInPublishError, XPublishError) as exc:
                post.status = PostStatus.failed
                post.error_log = str(exc)
                account.last_error = str(exc)
                logger.warning("Publishing failed for post %s: %s", post.id, exc)

        db.commit()
        return published_count

    except Exception:
        db.rollback()
        logger.exception("Publishing job failed")
        raise
    finally:
        db.close()
