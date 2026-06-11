from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from app.auth import decode_access_token, get_current_user
from app.models import get_db, Platform, Post, PostStatus
from app.models.brand import BrandSettings
from app.models.user import User
from app.services.image_service import ImageGenerationError, fetch_generated_image, get_post_image_url
from app.services.openai_service import generate_post
from app.tasks.post_tasks import auto_generate_posts

router = APIRouter(prefix="/posts", tags=["posts"])


class GeneratePostRequest(BaseModel):
    platform: str
    topic: Optional[str] = None
    brand_id: int
    brand_voice: Optional[str] = None
    hashtags: Optional[str] = None


class UpdatePostRequest(BaseModel):
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    status: Optional[PostStatus] = None
    brand_id: Optional[int] = None


class DuplicatePostRequest(BaseModel):
    platforms: list[str]


def to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def get_brand_or_404(db: Session, brand_id: int, current_user: User) -> BrandSettings:
    brand = (
        db.query(BrandSettings)
        .filter(BrandSettings.id == brand_id)
        .filter(BrandSettings.user_id == current_user.id)
        .first()
    )
    if not brand:
        raise HTTPException(status_code=404, detail="Business not found")
    return brand


def get_user_post_or_404(db: Session, post_id: int, current_user: User) -> Post:
    post = (
        db.query(Post)
        .join(BrandSettings, Post.brand_id == BrandSettings.id)
        .filter(Post.id == post_id)
        .filter(BrandSettings.user_id == current_user.id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


def get_default_topic(brand: BrandSettings) -> str:
    topics = [
        topic.strip()
        for topic in (brand.topics or "").split(",")
        if topic.strip()
    ]
    if not topics:
        raise HTTPException(
            status_code=400,
            detail="Add default content topics to this business before generating without a topic.",
        )

    return topics[datetime.now().timetuple().tm_yday % len(topics)]


def get_enabled_platforms(brand: BrandSettings) -> set[str]:
    platforms = {
        platform.strip().lower()
        for platform in (brand.enabled_platforms or "").split(",")
        if platform.strip()
    }
    return platforms or {"instagram", "facebook"}


@router.post("/generate")
def generate_and_save_post(
    request: GeneratePostRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brand = get_brand_or_404(db, request.brand_id, current_user)
    brand_voice = request.brand_voice or brand.brand_voice or "clear, trustworthy, and engaging"
    hashtags = request.hashtags or brand.hashtags or "#Business #SocialMedia #Marketing"
    topic = request.topic.strip() if request.topic else get_default_topic(brand)
    platform = request.platform.strip().lower()
    enabled_platforms = get_enabled_platforms(brand)

    if platform not in enabled_platforms:
        raise HTTPException(
            status_code=400,
            detail=f"{brand.company_name} is not configured for {platform}.",
        )

    content = generate_post(
        platform=platform,
        topic=topic,
        brand_voice=brand_voice,
        hashtags=hashtags,
        business_name=brand.company_name,
        industry=brand.industry,
        target_audience=brand.target_audience,
    )

    post = Post(
        brand_id=request.brand_id,
        platform=platform,
        caption=content["caption"],
        hashtags=content["hashtags"],
        image_prompt=content["image_prompt"],
        status=PostStatus.draft,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    post.image_url = get_post_image_url(post.id)
    db.commit()
    db.refresh(post)
    return post


@router.post("/generate-batch")
def trigger_batch_generation(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if brand_id is not None:
        get_brand_or_404(db, brand_id, current_user)

    count = auto_generate_posts(brand_id=brand_id, user_id=current_user.id)
    scope = "selected business" if brand_id else "all businesses"
    return {"message": f"Generated {count} posts for {scope}"}


@router.get("/")
def get_all_posts(
    status: Optional[str] = None,
    platform: Optional[str] = None,
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
        get_brand_or_404(db, brand_id, current_user)
        query = query.filter(Post.brand_id == brand_id)
    if status:
        query = query.filter(Post.status == status)
    if platform:
        query = query.filter(Post.platform == platform)
    return query.order_by(Post.created_at.desc()).all()


@router.post("/approve-all")
def approve_all_pending(
    brand_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = (
        db.query(Post)
        .join(BrandSettings, Post.brand_id == BrandSettings.id)
        .filter(BrandSettings.user_id == current_user.id)
        .filter(Post.status == PostStatus.pending_approval)
    )
    if brand_id is not None:
        get_brand_or_404(db, brand_id, current_user)
        query = query.filter(Post.brand_id == brand_id)

    posts = query.all()
    if not posts:
        return {"message": "No pending posts to approve"}

    for post in posts:
        post.status = PostStatus.approved

    db.commit()
    return {"message": f"Approved {len(posts)} posts"}


@router.get("/{post_id}")
def get_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_user_post_or_404(db, post_id, current_user)


@router.get("/{post_id}/image")
def get_post_image(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = get_user_post_or_404(db, post_id, current_user)

    try:
        image_content, media_type = fetch_generated_image(post.image_prompt or "", seed=post.id)
    except ImageGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=image_content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/{post_id}/publish-image")
def get_publish_image(post_id: int, token: str, db: Session = Depends(get_db)):
    token_subject = decode_access_token(token)
    if token_subject != f"post_image:{post_id}":
        raise HTTPException(status_code=401, detail="Invalid or expired image token")

    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    try:
        image_content, media_type = fetch_generated_image(post.image_prompt or "", seed=post.id)
    except ImageGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=image_content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/{post_id}/approve")
def approve_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = get_user_post_or_404(db, post_id, current_user)
    if post.status not in [PostStatus.draft, PostStatus.pending_approval]:
        raise HTTPException(status_code=400, detail=f"Cannot approve a post with status '{post.status}'")
    post.status = PostStatus.approved
    db.commit()
    db.refresh(post)
    return {"message": f"Post {post_id} approved", "post": post}


@router.post("/{post_id}/pause")
def pause_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = get_user_post_or_404(db, post_id, current_user)
    if post.status == PostStatus.paused:
        return {"message": f"Post {post_id} is already paused", "post": post}
    post.status_before_pause = post.status.value
    post.status = PostStatus.paused
    db.commit()
    db.refresh(post)
    return {"message": f"Post {post_id} paused", "post": post}


@router.post("/{post_id}/resume")
def resume_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = get_user_post_or_404(db, post_id, current_user)
    if post.status != PostStatus.paused:
        raise HTTPException(status_code=400, detail=f"Cannot resume a post with status '{post.status}'")

    previous_status = post.status_before_pause or PostStatus.approved.value
    try:
        post.status = PostStatus(previous_status)
    except ValueError:
        post.status = PostStatus.approved
    post.status_before_pause = None
    db.commit()
    db.refresh(post)
    return {"message": f"Post {post_id} resumed", "post": post}


@router.post("/{post_id}/duplicate")
def duplicate_post(
    post_id: int,
    request: DuplicatePostRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source_post = get_user_post_or_404(db, post_id, current_user)
    brand = get_brand_or_404(db, source_post.brand_id, current_user)
    enabled_platforms = get_enabled_platforms(brand)
    requested_platforms = []
    seen_platforms = set()

    for raw_platform in request.platforms:
        platform = raw_platform.strip().lower()
        if not platform or platform in seen_platforms:
            continue
        if platform == source_post.platform.value:
            raise HTTPException(status_code=400, detail=f"This post is already for {platform}.")
        if platform not in enabled_platforms:
            raise HTTPException(
                status_code=400,
                detail=f"{brand.company_name} is not configured for {platform}.",
            )
        try:
            requested_platforms.append(Platform(platform))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}") from exc
        seen_platforms.add(platform)

    if not requested_platforms:
        raise HTTPException(status_code=400, detail="Choose at least one platform to copy this post to.")

    duplicated_posts = []
    for platform in requested_platforms:
        duplicated_post = Post(
            brand_id=source_post.brand_id,
            platform=platform,
            caption=source_post.caption,
            hashtags=source_post.hashtags,
            image_prompt=source_post.image_prompt,
            image_url=source_post.image_url,
            status=PostStatus.draft,
        )
        db.add(duplicated_post)
        duplicated_posts.append(duplicated_post)

    db.commit()

    for duplicated_post in duplicated_posts:
        db.refresh(duplicated_post)

    return {
        "message": f"Copied post to {len(duplicated_posts)} platform(s)",
        "posts": duplicated_posts,
    }


@router.put("/{post_id}")
def update_post(
    post_id: int,
    request: UpdatePostRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = get_user_post_or_404(db, post_id, current_user)

    if request.brand_id is not None:
        get_brand_or_404(db, request.brand_id, current_user)
        post.brand_id = request.brand_id
    if request.caption:
        post.caption = request.caption
    if request.hashtags:
        post.hashtags = request.hashtags
    if request.scheduled_at:
        post.scheduled_at = to_utc_naive(request.scheduled_at)
    if request.status:
        post.status = request.status
        if request.status != PostStatus.paused:
            post.status_before_pause = None
        if request.status == PostStatus.scheduled:
            post.error_log = None
            post.published_at = None
            post.external_post_id = None
            post.platform_post_url = None

    db.commit()
    db.refresh(post)
    return {"message": f"Post {post_id} updated", "post": post}


@router.delete("/{post_id}")
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = get_user_post_or_404(db, post_id, current_user)
    db.delete(post)
    db.commit()
    return {"message": f"Post {post_id} deleted"}
