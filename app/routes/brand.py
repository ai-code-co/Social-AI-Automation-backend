from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator
from app.auth import get_current_user
from app.models import get_db
from app.models.brand import BrandSettings
from app.models.post import Platform, Post
from app.models.user import User

router = APIRouter(tags=["brands"])

ALLOWED_PLATFORMS = {platform.value for platform in Platform}


class BrandSettingsRequest(BaseModel):
    company_name: str
    industry: str = "general"
    tone: str = "professional"
    target_audience: str = "customers, followers, and potential buyers"
    brand_voice: str = "clear, trustworthy, and engaging"
    topics: str = "product updates, educational tips, community stories, offers"
    hashtags: str = "#Business #SocialMedia #Marketing"
    enabled_platforms: str = "instagram,facebook"

    @field_validator("topics")
    @classmethod
    def require_topics(cls, value):
        topics = [topic.strip() for topic in value.split(",") if topic.strip()]
        if not topics:
            raise ValueError("Add at least one default content topic")
        return ", ".join(topics)

    @field_validator("enabled_platforms")
    @classmethod
    def validate_enabled_platforms(cls, value):
        platforms = [platform.strip().lower() for platform in value.split(",") if platform.strip()]
        if not platforms:
            raise ValueError("Choose at least one platform")

        invalid_platforms = [platform for platform in platforms if platform not in ALLOWED_PLATFORMS]
        if invalid_platforms:
            raise ValueError(f"Unsupported platforms: {', '.join(invalid_platforms)}")

        return ",".join(dict.fromkeys(platforms))


def get_user_brand_or_404(db: Session, brand_id: int, current_user: User) -> BrandSettings:
    brand = (
        db.query(BrandSettings)
        .filter(BrandSettings.id == brand_id)
        .filter(BrandSettings.user_id == current_user.id)
        .first()
    )
    if not brand:
        raise HTTPException(status_code=404, detail="Business not found")
    return brand


@router.get("/brands/")
def list_brands(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return (
        db.query(BrandSettings)
        .filter(BrandSettings.user_id == current_user.id)
        .order_by(BrandSettings.created_at.desc())
        .all()
    )


@router.post("/brands/")
def create_brand(
    request: BrandSettingsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brand = BrandSettings(**request.model_dump(), user_id=current_user.id)
    db.add(brand)
    db.commit()
    db.refresh(brand)
    return brand


@router.get("/brands/{brand_id}")
def get_brand(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_user_brand_or_404(db, brand_id, current_user)


@router.put("/brands/{brand_id}")
def update_brand(
    brand_id: int,
    request: BrandSettingsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brand = get_user_brand_or_404(db, brand_id, current_user)

    for key, value in request.model_dump().items():
        setattr(brand, key, value)

    db.commit()
    db.refresh(brand)
    return brand


@router.delete("/brands/{brand_id}")
def delete_brand(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brand = get_user_brand_or_404(db, brand_id, current_user)

    post_count = db.query(Post).filter(Post.brand_id == brand_id).count()
    if post_count:
        raise HTTPException(
            status_code=400,
            detail="This business has posts. Delete or reassign its posts before deleting the business.",
        )

    db.delete(brand)
    db.commit()
    return {"message": f"Business {brand_id} deleted"}


# Backward-compatible routes for the earlier single-brand frontend.
@router.get("/brand/")
def get_first_brand(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    brand = (
        db.query(BrandSettings)
        .filter(BrandSettings.user_id == current_user.id)
        .order_by(BrandSettings.created_at.desc())
        .first()
    )
    if not brand:
        raise HTTPException(status_code=404, detail="Brand settings not found")
    return brand


@router.post("/brand/")
def save_first_brand(
    request: BrandSettingsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    brand = (
        db.query(BrandSettings)
        .filter(BrandSettings.user_id == current_user.id)
        .order_by(BrandSettings.created_at.desc())
        .first()
    )
    if not brand:
        brand = BrandSettings(**request.model_dump(), user_id=current_user.id)
        db.add(brand)
    else:
        for key, value in request.model_dump().items():
            setattr(brand, key, value)

    db.commit()
    db.refresh(brand)
    return brand
