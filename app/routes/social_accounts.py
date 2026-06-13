import json
import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from html import escape
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth import create_access_token, decode_access_token, get_current_user
from app.config import settings
from app.models import get_db
from app.models.brand import BrandSettings
from app.models.social_account import SocialAccount
from app.models.user import User


router = APIRouter(prefix="/social-accounts", tags=["social-accounts"])

META_PLATFORMS = {"facebook", "instagram"}
META_PLACEHOLDERS = {"", "your_meta_app_id", "your_meta_app_secret"}
SUPPORTED_PLATFORMS = {"facebook", "instagram", "linkedin", "twitter"}
INSTAGRAM_PLACEHOLDERS = {"", "your_instagram_app_id", "your_instagram_app_secret"}
LINKEDIN_PLACEHOLDERS = {"", "your_linkedin_client_id", "your_linkedin_client_secret"}
X_PLACEHOLDERS = {"", "your_x_client_id", "your_x_client_secret"}


class SocialAccountRequest(BaseModel):
    brand_id: int
    platform: str
    handle: str
    account_id: str
    access_token: str
    refresh_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    scopes: Optional[str] = None
    is_active: bool = True

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value):
        platform = value.strip().lower()
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError("Only facebook, instagram, linkedin, and twitter are supported for publishing right now")
        return platform


class SocialAccountUpdateRequest(BaseModel):
    handle: Optional[str] = None
    account_id: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    scopes: Optional[str] = None
    is_active: Optional[bool] = None


class MetaConnectPageRequest(BaseModel):
    page_token: str


def normalize_linkedin_author(account_id: str) -> str:
    account_id = account_id.strip()
    if account_id.startswith("urn:li:person:") or account_id.startswith("urn:li:organization:"):
        return account_id
    return f"urn:li:person:{account_id}"


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def serialize_account(account: SocialAccount) -> dict:
    return {
        "id": account.id,
        "brand_id": account.brand_id,
        "platform": account.platform,
        "handle": account.handle,
        "account_id": account.account_id,
        "token_expires_at": account.token_expires_at,
        "scopes": account.scopes,
        "is_active": account.is_active,
        "last_error": account.last_error,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
        "has_access_token": bool(account.access_token),
    }


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


def get_account_or_404(db: Session, account_id: int, current_user: User) -> SocialAccount:
    account = (
        db.query(SocialAccount)
        .join(BrandSettings, SocialAccount.brand_id == BrandSettings.id)
        .filter(SocialAccount.id == account_id)
        .filter(BrandSettings.user_id == current_user.id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Social account not found")
    return account


def graph_url(path: str) -> str:
    version = settings.meta_graph_version.strip().lstrip("/")
    return f"https://graph.facebook.com/{version}/{path.lstrip('/')}"


def validate_meta_settings(require_secret: bool = False):
    app_id = (settings.meta_app_id or "").strip()
    app_secret = (settings.meta_app_secret or "").strip()
    redirect_uri = (settings.meta_redirect_uri or "").strip()

    missing = []
    if app_id in META_PLACEHOLDERS:
        missing.append("META_APP_ID")
    if require_secret and app_secret in META_PLACEHOLDERS:
        missing.append("META_APP_SECRET")
    if not redirect_uri or redirect_uri.startswith("http:///"):
        missing.append("META_REDIRECT_URI")

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Meta OAuth is not configured correctly. Update: {', '.join(missing)}.",
        )


def validate_instagram_settings(require_secret: bool = False):
    app_id = (settings.instagram_app_id or "").strip()
    app_secret = (settings.instagram_app_secret or "").strip()
    redirect_uri = (settings.instagram_redirect_uri or "").strip()

    missing = []
    if app_id in INSTAGRAM_PLACEHOLDERS:
        missing.append("INSTAGRAM_APP_ID")
    if require_secret and app_secret in INSTAGRAM_PLACEHOLDERS:
        missing.append("INSTAGRAM_APP_SECRET")
    if not redirect_uri or redirect_uri.startswith("http:///"):
        missing.append("INSTAGRAM_REDIRECT_URI")

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Instagram OAuth is not configured correctly. Update: {', '.join(missing)}.",
        )


def validate_linkedin_settings(require_secret: bool = False):
    client_id = (settings.linkedin_client_id or "").strip()
    client_secret = (settings.linkedin_client_secret or "").strip()
    redirect_uri = (settings.linkedin_redirect_uri or "").strip()

    missing = []
    if client_id in LINKEDIN_PLACEHOLDERS:
        missing.append("LINKEDIN_CLIENT_ID")
    if require_secret and client_secret in LINKEDIN_PLACEHOLDERS:
        missing.append("LINKEDIN_CLIENT_SECRET")
    if not redirect_uri or redirect_uri.startswith("http:///"):
        missing.append("LINKEDIN_REDIRECT_URI")

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"LinkedIn OAuth is not configured correctly. Update: {', '.join(missing)}.",
        )


def validate_x_settings(require_secret: bool = False):
    client_id = (settings.x_client_id or "").strip()
    client_secret = (settings.x_client_secret or "").strip()
    redirect_uri = (settings.x_redirect_uri or "").strip()

    missing = []
    if client_id in X_PLACEHOLDERS:
        missing.append("X_CLIENT_ID")
    if require_secret and client_secret in X_PLACEHOLDERS:
        missing.append("X_CLIENT_SECRET")
    if not redirect_uri or redirect_uri.startswith("http:///"):
        missing.append("X_REDIRECT_URI")

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"X OAuth is not configured correctly. Update: {', '.join(missing)}.",
        )


def raise_for_meta_error(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Meta returned a non-JSON response with status {response.status_code}",
        ) from exc

    if response.is_error or "error" in data:
        error = data.get("error", {})
        raise HTTPException(status_code=400, detail=error.get("message") or "Meta request failed")

    return data


def raise_for_instagram_error(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.is_error or "error" in data:
        error = data.get("error", {})
        if isinstance(error, dict):
            message = error.get("message") or error.get("error_user_msg")
        else:
            message = data.get("error_description") or str(error)
        raise HTTPException(status_code=400, detail=message or "Instagram request failed")

    return data


def raise_for_linkedin_error(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.is_error:
        message = data.get("message") or data.get("error_description") or data.get("error") or response.text
        raise HTTPException(status_code=400, detail=message or "LinkedIn request failed")

    return data


def raise_for_x_error(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.is_error:
        errors = data.get("errors")
        message = data.get("detail") or data.get("error_description") or data.get("error") or response.text
        if errors and isinstance(errors, list):
            message = errors[0].get("message") or message
        raise HTTPException(status_code=400, detail=message or "X request failed")

    return data


def save_meta_account(
    db: Session,
    brand_id: int,
    platform: str,
    handle: str,
    account_id: str,
    access_token: str,
    scopes: str,
    token_expires_at: datetime | None = None,
) -> SocialAccount:
    existing = (
        db.query(SocialAccount)
        .filter(SocialAccount.brand_id == brand_id)
        .filter(SocialAccount.platform == platform)
        .first()
    )
    data = {
        "brand_id": brand_id,
        "platform": platform,
        "handle": handle,
        "account_id": account_id,
        "access_token": access_token,
        "scopes": scopes,
        "token_expires_at": token_expires_at,
        "is_active": True,
        "last_error": None,
    }

    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        return existing

    account = SocialAccount(**data)
    db.add(account)
    return account


def save_instagram_account(
    db: Session,
    brand_id: int,
    handle: str,
    account_id: str,
    access_token: str,
    scopes: str,
    token_expires_at: datetime | None = None,
) -> SocialAccount:
    existing = (
        db.query(SocialAccount)
        .filter(SocialAccount.brand_id == brand_id)
        .filter(SocialAccount.platform == "instagram")
        .first()
    )
    data = {
        "brand_id": brand_id,
        "platform": "instagram",
        "handle": handle,
        "account_id": account_id,
        "access_token": access_token,
        "scopes": scopes,
        "token_expires_at": token_expires_at,
        "is_active": True,
        "last_error": None,
    }

    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        return existing

    account = SocialAccount(**data)
    db.add(account)
    return account


def save_linkedin_account(
    db: Session,
    brand_id: int,
    handle: str,
    account_id: str,
    access_token: str,
    scopes: str,
    token_expires_at: datetime | None = None,
) -> SocialAccount:
    existing = (
        db.query(SocialAccount)
        .filter(SocialAccount.brand_id == brand_id)
        .filter(SocialAccount.platform == "linkedin")
        .first()
    )
    data = {
        "brand_id": brand_id,
        "platform": "linkedin",
        "handle": handle,
        "account_id": normalize_linkedin_author(account_id),
        "access_token": access_token,
        "scopes": scopes,
        "token_expires_at": token_expires_at,
        "is_active": True,
        "last_error": None,
    }

    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        return existing

    account = SocialAccount(**data)
    db.add(account)
    return account


def save_x_account(
    db: Session,
    brand_id: int,
    handle: str,
    account_id: str,
    access_token: str,
    refresh_token: str | None,
    scopes: str,
    token_expires_at: datetime | None = None,
) -> SocialAccount:
    existing = (
        db.query(SocialAccount)
        .filter(SocialAccount.brand_id == brand_id)
        .filter(SocialAccount.platform == "twitter")
        .first()
    )
    data = {
        "brand_id": brand_id,
        "platform": "twitter",
        "handle": handle,
        "account_id": account_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "scopes": scopes,
        "token_expires_at": token_expires_at,
        "is_active": True,
        "last_error": None,
    }

    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        return existing

    account = SocialAccount(**data)
    db.add(account)
    return account


def render_meta_message(title: str, message: str, success: bool = False) -> HTMLResponse:
    return render_oauth_message(title, message, success, "meta")


def render_instagram_message(title: str, message: str, success: bool = False) -> HTMLResponse:
    return render_oauth_message(title, message, success, "instagram")


def render_linkedin_message(title: str, message: str, success: bool = False) -> HTMLResponse:
    return render_oauth_message(title, message, success, "linkedin")


def render_x_message(title: str, message: str, success: bool = False) -> HTMLResponse:
    return render_oauth_message(title, message, success, "x")


def render_oauth_message(title: str, message: str, success: bool, provider: str) -> HTMLResponse:
    color = "#0f766e" if success else "#be123c"
    post_message_type = f"{provider}-connected" if success else f"{provider}-connect-error"
    return HTMLResponse(
        f"""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>{escape(title)}</title>
            <style>
              body {{
                margin: 0;
                min-height: 100vh;
                display: grid;
                place-items: center;
                font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #f6f7fb;
                color: #0f172a;
              }}
              main {{
                width: min(92vw, 28rem);
                border: 1px solid #e2e8f0;
                border-radius: 0.75rem;
                background: white;
                padding: 1.5rem;
                box-shadow: 0 24px 60px rgba(15, 23, 42, 0.14);
              }}
              h1 {{ margin: 0 0 0.5rem; font-size: 1.25rem; color: {color}; }}
              p {{ margin: 0; line-height: 1.6; color: #475569; }}
              button {{
                margin-top: 1rem;
                width: 100%;
                min-height: 2.75rem;
                border: 0;
                border-radius: 0.5rem;
                background: #0f172a;
                color: white;
                font-weight: 700;
                cursor: pointer;
              }}
            </style>
          </head>
          <body>
            <main>
              <h1>{escape(title)}</h1>
              <p>{escape(message)}</p>
              <button onclick="window.close()">Close</button>
            </main>
            <script>
              if (window.opener) {{
                window.opener.postMessage({{ type: "{post_message_type}" }}, "*");
              }}
            </script>
          </body>
        </html>
        """
    )


def render_page_picker(pages: list[dict]) -> HTMLResponse:
    page_cards = []
    for page in pages:
        page_cards.append(
            f"""
            <button class="page-card" type="button" data-token="{escape(page['page_token'])}">
              <span class="page-name">{escape(page['name'])}</span>
              <span class="page-meta">Facebook Page ID: {escape(page['id'])}</span>
            </button>
            """
        )

    return HTMLResponse(
        f"""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>Choose Meta Page</title>
            <style>
              body {{
                margin: 0;
                min-height: 100vh;
                font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #f6f7fb;
                color: #0f172a;
              }}
              main {{
                width: min(92vw, 34rem);
                margin: 0 auto;
                padding: 1.25rem 0;
              }}
              .panel {{
                border: 1px solid #e2e8f0;
                border-radius: 0.75rem;
                background: white;
                padding: 1rem;
                box-shadow: 0 24px 60px rgba(15, 23, 42, 0.14);
              }}
              h1 {{ margin: 0; font-size: 1.25rem; }}
              p {{ margin: 0.4rem 0 1rem; color: #64748b; line-height: 1.5; }}
              .page-card {{
                display: block;
                width: 100%;
                min-height: 5.25rem;
                margin-top: 0.75rem;
                padding: 0.9rem;
                border: 1px solid #cbd5e1;
                border-radius: 0.625rem;
                background: #f8fafc;
                color: #0f172a;
                text-align: left;
                cursor: pointer;
              }}
              .page-card:hover {{ border-color: #14b8a6; background: #f0fdfa; }}
              .page-name {{ display: block; font-weight: 800; }}
              .page-meta {{ display: block; margin-top: 0.35rem; font-size: 0.8125rem; color: #64748b; }}
              .status {{ margin-top: 1rem; min-height: 1.5rem; font-size: 0.875rem; color: #0f766e; }}
            </style>
          </head>
          <body>
            <main>
              <div class="panel">
                <h1>Choose a Facebook Page</h1>
                <p>The selected Facebook Page will be connected for publishing. Instagram is connected separately with Instagram Business Login.</p>
                {"".join(page_cards)}
                <div class="status" id="status"></div>
              </div>
            </main>
            <script>
              const statusEl = document.getElementById("status");
              document.querySelectorAll(".page-card").forEach((button) => {{
                button.addEventListener("click", async () => {{
                  statusEl.textContent = "Connecting selected Page...";
                  document.querySelectorAll(".page-card").forEach((item) => item.disabled = true);
                  try {{
                    const response = await fetch("/social-accounts/meta/connect-page", {{
                      method: "POST",
                      headers: {{ "Content-Type": "application/json" }},
                      body: JSON.stringify({{ page_token: button.dataset.token }}),
                    }});
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || "Unable to connect Page");
                    statusEl.textContent = data.message || "Connected";
                    if (window.opener) {{
                      window.opener.postMessage({{ type: "meta-connected" }}, "*");
                    }}
                    setTimeout(() => window.close(), 900);
                  }} catch (error) {{
                    statusEl.style.color = "#be123c";
                    statusEl.textContent = error.message;
                    document.querySelectorAll(".page-card").forEach((item) => item.disabled = false);
                  }}
                }});
              }});
            </script>
          </body>
        </html>
        """
    )


@router.get("/")
def list_social_accounts(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_brand_or_404(db, brand_id, current_user)
    accounts = (
        db.query(SocialAccount)
        .filter(SocialAccount.brand_id == brand_id)
        .order_by(SocialAccount.platform.asc(), SocialAccount.created_at.desc())
        .all()
    )
    return [serialize_account(account) for account in accounts]


@router.post("/")
def create_social_account(
    request: SocialAccountRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_brand_or_404(db, request.brand_id, current_user)
    existing = (
        db.query(SocialAccount)
        .filter(SocialAccount.brand_id == request.brand_id)
        .filter(SocialAccount.platform == request.platform)
        .first()
    )
     
    if existing:
        for key, value in request.model_dump().items():
            setattr(existing, key, value)
        db.commit()
        db.refresh(existing)
        return serialize_account(existing)

    account = SocialAccount(**request.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return serialize_account(account)


@router.put("/{account_id}")
def update_social_account(
    account_id: int,
    request: SocialAccountUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    account = get_account_or_404(db, account_id, current_user)

    for key, value in request.model_dump(exclude_unset=True).items():
        setattr(account, key, value)

    db.commit()
    db.refresh(account)
    return serialize_account(account)


@router.delete("/{account_id}")
def delete_social_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    account = get_account_or_404(db, account_id, current_user)

    db.delete(account)
    db.commit()
    return {"message": "Social account disconnected"}


@router.get("/meta/oauth-url")
def get_meta_oauth_url(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_brand_or_404(db, brand_id, current_user)
    validate_meta_settings()

    state = create_access_token(
        json.dumps({"type": "meta_oauth", "user_id": current_user.id, "brand_id": brand_id}),
        expires_delta=timedelta(minutes=15),
    )
    scopes = [
        "pages_manage_posts",
        "pages_read_engagement",
        "pages_show_list",
        "business_management",
        "read_insights",
    ]
    params = urlencode(
        {
            "client_id": settings.meta_app_id,
            "redirect_uri": settings.meta_redirect_uri,
            "state": state,
            "scope": ",".join(scopes),
            "response_type": "code",
        }
    )
    return {
        "url": f"https://www.facebook.com/{settings.meta_graph_version}/dialog/oauth?{params}"
    }


@router.get("/instagram/oauth-url")
def get_instagram_oauth_url(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_brand_or_404(db, brand_id, current_user)
    validate_instagram_settings()

    state = create_access_token(
        json.dumps({"type": "instagram_oauth", "user_id": current_user.id, "brand_id": brand_id}),
        expires_delta=timedelta(minutes=15),
    )
    params = urlencode(
        {
            "client_id": settings.instagram_app_id,
            "redirect_uri": settings.instagram_redirect_uri,
            "state": state,
            "scope": "instagram_business_basic,instagram_business_content_publish",
            "response_type": "code",
        }
    )
    return {"url": f"https://www.instagram.com/oauth/authorize?{params}"}


@router.get("/linkedin/oauth-url")
def get_linkedin_oauth_url(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_brand_or_404(db, brand_id, current_user)
    validate_linkedin_settings()

    state = create_access_token(
        json.dumps({"type": "linkedin_oauth", "user_id": current_user.id, "brand_id": brand_id}),
        expires_delta=timedelta(minutes=15),
    )
    params = urlencode(
        {
            "response_type": "code",
            "client_id": settings.linkedin_client_id,
            "redirect_uri": settings.linkedin_redirect_uri,
            "state": state,
            "scope": "openid profile w_member_social",
        }
    )
    return {"url": f"https://www.linkedin.com/oauth/v2/authorization?{params}"}


@router.get("/x/oauth-url")
def get_x_oauth_url(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_brand_or_404(db, brand_id, current_user)
    validate_x_settings()

    code_verifier, code_challenge = create_pkce_pair()
    state = create_access_token(
        json.dumps(
            {
                "type": "x_oauth",
                "user_id": current_user.id,
                "brand_id": brand_id,
                "code_verifier": code_verifier,
            }
        ),
        expires_delta=timedelta(minutes=15),
    )
    params = urlencode(
        {
            "response_type": "code",
            "client_id": settings.x_client_id,
            "redirect_uri": settings.x_redirect_uri,
            "state": state,
            "scope": "tweet.read tweet.write users.read offline.access",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return {"url": f"https://x.com/i/oauth2/authorize?{params}"}


@router.get("/x/callback", response_class=HTMLResponse)
def x_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        return render_x_message("X connection cancelled", error_description or error)
    if not code or not state:
        return render_x_message("X connection failed", "X did not return the required authorization code.")

    state_subject = decode_access_token(state)
    if not state_subject:
        return render_x_message("X connection expired", "Please start the connection again from the Social tab.")

    try:
        state_data = json.loads(state_subject)
    except json.JSONDecodeError:
        return render_x_message("X connection failed", "The X connection state is invalid.")

    user_id = state_data.get("user_id")
    brand_id = state_data.get("brand_id")
    code_verifier = state_data.get("code_verifier")
    if state_data.get("type") != "x_oauth" or not user_id or not brand_id or not code_verifier:
        return render_x_message("X connection failed", "The X connection state is invalid.")

    brand = db.query(BrandSettings).filter(BrandSettings.id == brand_id, BrandSettings.user_id == user_id).first()
    if not brand:
        return render_x_message("Business not found", "This X connection does not match an active business.")
    try:
        validate_x_settings(require_secret=True)
    except HTTPException as exc:
        return render_x_message("X is not configured", str(exc.detail))

    auth_value = base64.b64encode(f"{settings.x_client_id}:{settings.x_client_secret}".encode("utf-8")).decode("ascii")
    try:
        token_response = httpx.post(
            "https://api.x.com/2/oauth2/token",
            data={
                "code": code,
                "grant_type": "authorization_code",
                "client_id": settings.x_client_id,
                "redirect_uri": settings.x_redirect_uri,
                "code_verifier": code_verifier,
            },
            headers={
                "Authorization": f"Basic {auth_value}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
        token_data = raise_for_x_error(token_response)
        access_token = token_data.get("access_token")
        if not access_token:
            return render_x_message("X connection failed", "X did not return an access token.")

        user_response = httpx.get(
            "https://api.x.com/2/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        user_data = raise_for_x_error(user_response).get("data") or {}
    except HTTPException as exc:
        return render_x_message("X connection failed", str(exc.detail))
    except httpx.HTTPError as exc:
        return render_x_message("X connection failed", f"Unable to reach X: {exc}")

    x_user_id = user_data.get("id")
    if not x_user_id:
        return render_x_message("X connection failed", "X did not return a user identifier.")

    token_expires_at = None
    if token_data.get("expires_in"):
        token_expires_at = datetime.utcnow() + timedelta(seconds=int(token_data["expires_in"]))

    username = user_data.get("username") or x_user_id
    handle = f"@{username}" if not str(username).startswith("@") else username
    save_x_account(
        db=db,
        brand_id=brand_id,
        handle=handle,
        account_id=x_user_id,
        access_token=access_token,
        refresh_token=token_data.get("refresh_token"),
        scopes=token_data.get("scope") or "tweet.read tweet.write users.read offline.access",
        token_expires_at=token_expires_at,
    )
    db.commit()
    return render_x_message("X connected", f"Connected {handle} for publishing.", success=True)


@router.get("/instagram/callback", response_class=HTMLResponse)
def instagram_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        return render_instagram_message("Instagram connection cancelled", error_description or error)
    if not code or not state:
        return render_instagram_message("Instagram connection failed", "Instagram did not return the required authorization code.")

    state_subject = decode_access_token(state)
    if not state_subject:
        return render_instagram_message("Instagram connection expired", "Please start the connection again from the Social tab.")

    try:
        state_data = json.loads(state_subject)
    except json.JSONDecodeError:
        return render_instagram_message("Instagram connection failed", "The Instagram connection state is invalid.")

    user_id = state_data.get("user_id")
    brand_id = state_data.get("brand_id")
    if state_data.get("type") != "instagram_oauth" or not user_id or not brand_id:
        return render_instagram_message("Instagram connection failed", "The Instagram connection state is invalid.")

    brand = db.query(BrandSettings).filter(BrandSettings.id == brand_id, BrandSettings.user_id == user_id).first()
    if not brand:
        return render_instagram_message("Business not found", "This Instagram connection does not match an active business.")
    try:
        validate_instagram_settings(require_secret=True)
    except HTTPException as exc:
        return render_instagram_message("Instagram is not configured", str(exc.detail))

    try:
        token_response = httpx.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": settings.instagram_app_id,
                "client_secret": settings.instagram_app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": settings.instagram_redirect_uri,
                "code": code,
            },
            timeout=30,
        )
        token_data = raise_for_instagram_error(token_response)
        access_token = token_data.get("access_token")
        if not access_token:
            return render_instagram_message("Instagram connection failed", "Instagram did not return an access token.")

        long_lived_response = httpx.get(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.instagram_app_secret,
                "access_token": access_token,
            },
            timeout=30,
        )
        long_lived_data = raise_for_instagram_error(long_lived_response)
        access_token = long_lived_data.get("access_token") or access_token

        profile_response = httpx.get(
            "https://graph.instagram.com/me",
            params={
                "fields": "id,username,account_type",
                "access_token": access_token,
            },
            timeout=30,
        )
        profile = raise_for_instagram_error(profile_response)
    except HTTPException as exc:
        return render_instagram_message("Instagram connection failed", str(exc.detail))
    except httpx.HTTPError as exc:
        return render_instagram_message("Instagram connection failed", f"Unable to reach Instagram: {exc}")

    instagram_account_id = str(token_data.get("user_id") or profile.get("id") or "")
    if not instagram_account_id:
        return render_instagram_message("Instagram connection failed", "Instagram did not return an account ID.")

    token_expires_at = None
    if long_lived_data.get("expires_in"):
        token_expires_at = datetime.utcnow() + timedelta(seconds=int(long_lived_data["expires_in"]))

    username = profile.get("username") or instagram_account_id
    handle = f"@{username}" if not str(username).startswith("@") else username
    save_instagram_account(
        db=db,
        brand_id=brand_id,
        handle=handle,
        account_id=instagram_account_id,
        access_token=access_token,
        scopes="instagram_business_basic,instagram_business_content_publish",
        token_expires_at=token_expires_at,
    )
    db.commit()
    return render_instagram_message("Instagram connected", f"Connected {handle} for publishing.", success=True)


@router.get("/linkedin/callback", response_class=HTMLResponse)
def linkedin_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        return render_linkedin_message("LinkedIn connection cancelled", error_description or error)
    if not code or not state:
        return render_linkedin_message("LinkedIn connection failed", "LinkedIn did not return the required authorization code.")

    state_subject = decode_access_token(state)
    if not state_subject:
        return render_linkedin_message("LinkedIn connection expired", "Please start the connection again from the Social tab.")

    try:
        state_data = json.loads(state_subject)
    except json.JSONDecodeError:
        return render_linkedin_message("LinkedIn connection failed", "The LinkedIn connection state is invalid.")

    user_id = state_data.get("user_id")
    brand_id = state_data.get("brand_id")
    if state_data.get("type") != "linkedin_oauth" or not user_id or not brand_id:
        return render_linkedin_message("LinkedIn connection failed", "The LinkedIn connection state is invalid.")

    brand = db.query(BrandSettings).filter(BrandSettings.id == brand_id, BrandSettings.user_id == user_id).first()
    if not brand:
        return render_linkedin_message("Business not found", "This LinkedIn connection does not match an active business.")
    try:
        validate_linkedin_settings(require_secret=True)
    except HTTPException as exc:
        return render_linkedin_message("LinkedIn is not configured", str(exc.detail))

    try:
        token_response = httpx.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.linkedin_redirect_uri,
                "client_id": settings.linkedin_client_id,
                "client_secret": settings.linkedin_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        token_data = raise_for_linkedin_error(token_response)
        access_token = token_data.get("access_token")
        if not access_token:
            return render_linkedin_message("LinkedIn connection failed", "LinkedIn did not return an access token.")

        userinfo_response = httpx.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        userinfo = raise_for_linkedin_error(userinfo_response)
    except HTTPException as exc:
        return render_linkedin_message("LinkedIn connection failed", str(exc.detail))
    except httpx.HTTPError as exc:
        return render_linkedin_message("LinkedIn connection failed", f"Unable to reach LinkedIn: {exc}")

    linkedin_subject = userinfo.get("sub")
    if not linkedin_subject:
        return render_linkedin_message("LinkedIn connection failed", "LinkedIn did not return a member identifier.")

    token_expires_at = None
    if token_data.get("expires_in"):
        token_expires_at = datetime.utcnow() + timedelta(seconds=int(token_data["expires_in"]))

    handle = userinfo.get("name") or userinfo.get("preferred_username") or userinfo.get("email") or linkedin_subject
    save_linkedin_account(
        db=db,
        brand_id=brand_id,
        handle=handle,
        account_id=linkedin_subject,
        access_token=access_token,
        scopes=token_data.get("scope") or "openid profile w_member_social",
        token_expires_at=token_expires_at,
    )
    db.commit()
    return render_linkedin_message("LinkedIn connected", f"Connected {handle} for publishing.", success=True)


@router.get("/meta/callback", response_class=HTMLResponse)
def meta_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_message: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        return render_meta_message("Meta connection cancelled", error_message or error)
    if not code or not state:
        return render_meta_message("Meta connection failed", "Meta did not return the required authorization code.")

    state_subject = decode_access_token(state)
    if not state_subject:
        return render_meta_message("Meta connection expired", "Please start the connection again from the Social tab.")

    try:
        state_data = json.loads(state_subject)
    except json.JSONDecodeError:
        return render_meta_message("Meta connection failed", "The Meta connection state is invalid.")

    user_id = state_data.get("user_id")
    brand_id = state_data.get("brand_id")
    if state_data.get("type") != "meta_oauth" or not user_id or not brand_id:
        return render_meta_message("Meta connection failed", "The Meta connection state is invalid.")

    brand = db.query(BrandSettings).filter(BrandSettings.id == brand_id, BrandSettings.user_id == user_id).first()
    if not brand:
        return render_meta_message("Business not found", "This Meta connection does not match an active business.")
    try:
        validate_meta_settings(require_secret=True)
    except HTTPException as exc:
        return render_meta_message("Meta is not configured", str(exc.detail))

    try:
        token_response = httpx.get(
            graph_url("oauth/access_token"),
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": settings.meta_redirect_uri,
                "code": code,
            },
            timeout=30,
        )
        token_data = raise_for_meta_error(token_response)
        user_access_token = token_data.get("access_token")
        if not user_access_token:
            return render_meta_message("Meta connection failed", "Meta did not return an access token.")

        long_lived_response = httpx.get(
            graph_url("oauth/access_token"),
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": user_access_token,
            },
            timeout=30,
        )
        long_lived_data = raise_for_meta_error(long_lived_response)
        user_access_token = long_lived_data.get("access_token") or user_access_token
        expires_in = long_lived_data.get("expires_in")

        page_fields = "id,name,access_token,tasks"
        pages_response = httpx.get(
            graph_url("me/accounts"),
            params={
                "fields": page_fields,
                "access_token": user_access_token,
            },
            timeout=30,
        )
        pages_data = raise_for_meta_error(pages_response)
    except HTTPException as exc:
        return render_meta_message("Meta connection failed", str(exc.detail))
    except httpx.HTTPError as exc:
        return render_meta_message("Meta connection failed", f"Unable to reach Meta: {exc}")

    token_expires_at = None
    if expires_in:
        token_expires_at = (datetime.utcnow() + timedelta(seconds=int(expires_in))).isoformat()

    pages = []
    for page in pages_data.get("data", []):
        page_access_token = page.get("access_token")
        if not page.get("id") or not page.get("name") or not page_access_token:
            continue

        page_payload = {
            "type": "meta_page",
            "user_id": user_id,
            "brand_id": brand_id,
            "id": page["id"],
            "name": page["name"],
            "access_token": page_access_token,
            "scopes": "pages_manage_posts,pages_read_engagement,pages_show_list",
            "token_expires_at": token_expires_at,
        }
        page["page_token"] = create_access_token(
            json.dumps(page_payload),
            expires_delta=timedelta(minutes=15),
        )
        pages.append(page)

    if not pages:
        return render_meta_message(
            "No Pages found",
            "Meta did not return any Facebook Pages with publishing access for this account.",
        )

    return render_page_picker(pages)


@router.post("/meta/connect-page")
def connect_meta_page(request: MetaConnectPageRequest, db: Session = Depends(get_db)):
    page_subject = decode_access_token(request.page_token)
    if not page_subject:
        raise HTTPException(status_code=401, detail="This Meta page selection has expired. Start again from the Social tab.")

    try:
        page_data = json.loads(page_subject)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Meta page selection") from exc

    if page_data.get("type") != "meta_page":
        raise HTTPException(status_code=400, detail="Invalid Meta page selection")

    brand_id = page_data["brand_id"]
    user_id = page_data["user_id"]
    brand = db.query(BrandSettings).filter(BrandSettings.id == brand_id, BrandSettings.user_id == user_id).first()
    if not brand:
        raise HTTPException(status_code=404, detail="Business not found")

    token_expires_at = None
    if page_data.get("token_expires_at"):
        token_expires_at = datetime.fromisoformat(page_data["token_expires_at"])

    save_meta_account(
        db=db,
        brand_id=brand_id,
        platform="facebook",
        handle=page_data["name"],
        account_id=page_data["id"],
        access_token=page_data["access_token"],
        scopes=page_data.get("scopes") or "",
        token_expires_at=token_expires_at,
    )

    db.commit()
    return {"message": "Connected Facebook Page"}
