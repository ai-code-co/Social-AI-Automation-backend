from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.models import get_db, Post, PostStatus
from app.models.brand import BrandSettings
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.meta_metrics import MetricsSyncError, sync_meta_post_metrics


router = APIRouter(prefix="/performance", tags=["performance"])

SYNC_PLATFORMS = {"facebook", "instagram"}


def metric_value(value: int | None) -> int:
    return value or 0


def engagement_for_post(post: Post) -> int:
    return (
        metric_value(post.likes_count)
        + metric_value(post.comments_count)
        + metric_value(post.shares_count)
        + metric_value(post.clicks_count)
    )


def engagement_rate(engagement: int, views: int) -> float:
    if views <= 0:
        return 0
    return round((engagement / views) * 100, 2)


def serialize_post_metrics(post: Post) -> dict:
    views = metric_value(post.views_count)
    engagement = engagement_for_post(post)
    return {
        "id": post.id,
        "brand_id": post.brand_id,
        "platform": post.platform.value,
        "caption": post.caption,
        "status": post.status.value,
        "published_at": post.published_at,
        "platform_post_url": post.platform_post_url,
        "views_count": views,
        "likes_count": metric_value(post.likes_count),
        "comments_count": metric_value(post.comments_count),
        "shares_count": metric_value(post.shares_count),
        "clicks_count": metric_value(post.clicks_count),
        "engagement_count": engagement,
        "engagement_rate": engagement_rate(engagement, views),
    }


@router.get("/summary")
def get_performance_summary(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = (
        db.query(Post)
        .join(BrandSettings, Post.brand_id == BrandSettings.id)
        .filter(BrandSettings.user_id == current_user.id)
    )

    if brand_id is not None:
        brand = (
            db.query(BrandSettings)
            .filter(BrandSettings.id == brand_id)
            .filter(BrandSettings.user_id == current_user.id)
            .first()
        )
        if not brand:
            raise HTTPException(status_code=404, detail="Business not found")
        query = query.filter(Post.brand_id == brand_id)

    posts = query.order_by(Post.published_at.desc().nullslast(), Post.created_at.desc()).all()
    post_metrics = [serialize_post_metrics(post) for post in posts]

    totals = {
        "posts_count": len(posts),
        "published_count": sum(1 for post in posts if post.status == PostStatus.published),
        "views_count": sum(post["views_count"] for post in post_metrics),
        "likes_count": sum(post["likes_count"] for post in post_metrics),
        "comments_count": sum(post["comments_count"] for post in post_metrics),
        "shares_count": sum(post["shares_count"] for post in post_metrics),
        "clicks_count": sum(post["clicks_count"] for post in post_metrics),
    }
    totals["engagement_count"] = (
        totals["likes_count"]
        + totals["comments_count"]
        + totals["shares_count"]
        + totals["clicks_count"]
    )
    totals["engagement_rate"] = engagement_rate(totals["engagement_count"], totals["views_count"])

    platform_totals = defaultdict(lambda: {
        "platform": "",
        "posts_count": 0,
        "published_count": 0,
        "views_count": 0,
        "likes_count": 0,
        "comments_count": 0,
        "shares_count": 0,
        "clicks_count": 0,
        "engagement_count": 0,
        "engagement_rate": 0,
    })

    for post in post_metrics:
        platform = post["platform"]
        platform_data = platform_totals[platform]
        platform_data["platform"] = platform
        platform_data["posts_count"] += 1
        platform_data["published_count"] += 1 if post["status"] == PostStatus.published.value else 0
        for key in ["views_count", "likes_count", "comments_count", "shares_count", "clicks_count", "engagement_count"]:
            platform_data[key] += post[key]

    platforms = []
    for platform_data in platform_totals.values():
        platform_data["engagement_rate"] = engagement_rate(
            platform_data["engagement_count"],
            platform_data["views_count"],
        )
        platforms.append(platform_data)

    platforms.sort(key=lambda item: item["engagement_count"], reverse=True)
    top_posts = sorted(post_metrics, key=lambda item: item["engagement_count"], reverse=True)[:5]

    return {
        "totals": totals,
        "platforms": platforms,
        "posts": post_metrics,
        "top_posts": top_posts,
    }


@router.post("/sync")
def sync_performance_metrics(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brands_query = db.query(BrandSettings).filter(BrandSettings.user_id == current_user.id)
    if brand_id is not None:
        brands_query = brands_query.filter(BrandSettings.id == brand_id)

    brands = brands_query.all()
    if brand_id is not None and not brands:
        raise HTTPException(status_code=404, detail="Business not found")

    brand_ids = [brand.id for brand in brands]
    if not brand_ids:
        return {"updated": 0, "failed": 0, "skipped": 0, "errors": []}

    candidate_posts = (
        db.query(Post)
        .filter(Post.brand_id.in_(brand_ids))
        .filter(Post.status == PostStatus.published)
        .all()
    )
    missing_external_ids = [
        {"post_id": post.id, "platform": post.platform.value}
        for post in candidate_posts
        if post.platform.value in SYNC_PLATFORMS and not post.external_post_id
    ]

    posts = (
        db.query(Post)
        .filter(Post.brand_id.in_(brand_ids))
        .filter(Post.status == PostStatus.published)
        .filter(Post.external_post_id.isnot(None))
        .all()
    )

    accounts = (
        db.query(SocialAccount)
        .filter(SocialAccount.brand_id.in_(brand_ids))
        .filter(SocialAccount.is_active.is_(True))
        .all()
    )
    account_by_brand_platform = {
        (account.brand_id, account.platform): account
        for account in accounts
    }

    updated = 0
    failed = 0
    skipped = 0
    errors = []

    for post in posts:
        platform = post.platform.value
        if platform not in SYNC_PLATFORMS:
            skipped += 1
            continue

        account = account_by_brand_platform.get((post.brand_id, platform))
        if not account:
            skipped += 1
            continue

        try:
            metrics = sync_meta_post_metrics(post, account)
        except MetricsSyncError as exc:
            failed += 1
            errors.append({"post_id": post.id, "platform": platform, "error": str(exc)})
            continue

        post.views_count = metrics["views_count"]
        post.likes_count = metrics["likes_count"]
        post.comments_count = metrics["comments_count"]
        post.shares_count = metrics["shares_count"]
        post.clicks_count = metrics["clicks_count"]
        updated += 1

    db.commit()

    return {
        "updated": updated,
        "failed": failed,
        "skipped": skipped + len(missing_external_ids),
        "errors": errors,
        "missing_external_ids": missing_external_ids[:10],
    }
