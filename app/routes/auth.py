import json
import secrets
from datetime import timedelta
from html import escape
from urllib.parse import urlencode
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth import create_access_token, decode_access_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.models import get_db
from app.models.user import User


router = APIRouter(prefix="/auth", tags=["auth"])
GOOGLE_PLACEHOLDERS = {"", "your_google_client_id", "your_google_client_secret"}


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        email = value.strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise ValueError("Enter a valid email address")
        return email

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters")
        return value


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower()


def serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
    }


def auth_response(user: User) -> dict:
    return {
        "access_token": create_access_token(str(user.id)),
        "token_type": "bearer",
        "user": serialize_user(user),
    }


def validate_google_settings(require_secret: bool = False):
    client_id = (settings.google_client_id or "").strip()
    client_secret = (settings.google_client_secret or "").strip()
    redirect_uri = (settings.google_redirect_uri or "").strip()

    missing = []
    if client_id in GOOGLE_PLACEHOLDERS:
        missing.append("GOOGLE_CLIENT_ID")
    if require_secret and client_secret in GOOGLE_PLACEHOLDERS:
        missing.append("GOOGLE_CLIENT_SECRET")
    if not redirect_uri or redirect_uri.startswith("http:///"):
        missing.append("GOOGLE_REDIRECT_URI")

    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Google OAuth is not configured correctly. Update: {', '.join(missing)}.",
        )


def raise_for_google_error(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.is_error:
        message = data.get("error_description") or data.get("error") or response.text
        raise HTTPException(status_code=400, detail=message or "Google request failed")

    return data


def render_google_message(title: str, message: str, auth_payload: dict | None = None) -> HTMLResponse:
    success = auth_payload is not None
    color = "#0f766e" if success else "#be123c"
    payload_script = ""
    if auth_payload:
        payload_script = f"""
          if (window.opener) {{
            window.opener.postMessage({json.dumps({"type": "google-authenticated", "payload": auth_payload})}, "*");
          }}
          setTimeout(() => window.close(), 700);
        """
    else:
        payload_script = """
          if (window.opener) {
            window.opener.postMessage({ type: "google-auth-error" }, "*");
          }
        """

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
            <script>{payload_script}</script>
          </body>
        </html>
        """
    )


@router.post("/register")
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    user = User(
        email=request.email,
        full_name=request.full_name.strip() if request.full_name else None,
        hashed_password=hash_password(request.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return auth_response(user)


@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled")

    return auth_response(user)


@router.get("/me")
def read_current_user(current_user: User = Depends(get_current_user)):
    return serialize_user(current_user)


@router.get("/google/oauth-url")
def get_google_oauth_url():
    validate_google_settings()
    state = create_access_token(
        json.dumps({"type": "google_auth", "nonce": secrets.token_urlsafe(16)}),
        expires_delta=timedelta(minutes=15),
    )
    params = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
    )
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}"}


@router.get("/google/callback", response_class=HTMLResponse)
def google_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if error:
        return render_google_message("Google sign-in cancelled", error)
    if not code or not state:
        return render_google_message("Google sign-in failed", "Google did not return the required authorization code.")

    state_subject = decode_access_token(state)
    if not state_subject:
        return render_google_message("Google sign-in expired", "Please start Google sign-in again.")

    try:
        state_data = json.loads(state_subject)
    except json.JSONDecodeError:
        return render_google_message("Google sign-in failed", "The Google sign-in state is invalid.")

    if state_data.get("type") != "google_auth":
        return render_google_message("Google sign-in failed", "The Google sign-in state is invalid.")

    try:
        validate_google_settings(require_secret=True)
    except HTTPException as exc:
        return render_google_message("Google is not configured", str(exc.detail))

    try:
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        token_data = raise_for_google_error(token_response)
        access_token = token_data.get("access_token")
        if not access_token:
            return render_google_message("Google sign-in failed", "Google did not return an access token.")

        userinfo_response = httpx.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        userinfo = raise_for_google_error(userinfo_response)
    except HTTPException as exc:
        return render_google_message("Google sign-in failed", str(exc.detail))
    except httpx.HTTPError as exc:
        return render_google_message("Google sign-in failed", f"Unable to reach Google: {exc}")

    email = (userinfo.get("email") or "").strip().lower()
    google_id = userinfo.get("sub")
    email_verified = userinfo.get("email_verified")
    if not email or not google_id:
        return render_google_message("Google sign-in failed", "Google did not return an email address and account ID.")
    if email_verified is False:
        return render_google_message("Google sign-in failed", "Google did not confirm this email address.")

    user = db.query(User).filter(User.google_id == google_id).first()
    if not user:
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.google_id = google_id
            user.auth_provider = "google"
            if not user.full_name and userinfo.get("name"):
                user.full_name = userinfo["name"]
        else:
            user = User(
                email=email,
                full_name=userinfo.get("name"),
                hashed_password=hash_password(secrets.token_urlsafe(32)),
                google_id=google_id,
                auth_provider="google",
            )
            db.add(user)

    if not user.is_active:
        return render_google_message("Account disabled", "This account is disabled.")

    db.commit()
    db.refresh(user)
    return render_google_message("Google sign-in complete", "You can close this window.", auth_payload=auth_response(user))
