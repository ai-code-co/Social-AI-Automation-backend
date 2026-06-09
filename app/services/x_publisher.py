import base64
from datetime import datetime, timedelta

import httpx

from app.config import settings
from app.models.post import Post
from app.models.social_account import SocialAccount


class PublishError(Exception):
    pass


def _caption(post: Post) -> str:
    parts = [post.caption.strip()]
    if post.hashtags:
        parts.append(post.hashtags.strip())
    return "\n\n".join(part for part in parts if part)


def _raise_for_x_error(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.is_error:
        errors = data.get("errors")
        message = data.get("detail") or data.get("error_description") or data.get("error") or response.text
        if errors and isinstance(errors, list):
            message = errors[0].get("message") or message
        raise PublishError(message or "X request failed")

    return data


def _tweet_url(account: SocialAccount, tweet_id: str | None) -> str | None:
    if not tweet_id:
        return None
    handle = (account.handle or "").strip().lstrip("@") or "i"
    return f"https://x.com/{handle}/status/{tweet_id}"


def _refresh_access_token(account: SocialAccount):
    if not account.refresh_token:
        return
    if account.token_expires_at and account.token_expires_at > datetime.utcnow() + timedelta(minutes=5):
        return
    if not settings.x_client_id or not settings.x_client_secret:
        raise PublishError("X token refresh requires X_CLIENT_ID and X_CLIENT_SECRET.")

    auth_value = base64.b64encode(
        f"{settings.x_client_id}:{settings.x_client_secret}".encode("utf-8")
    ).decode("ascii")
    response = httpx.post(
        "https://api.x.com/2/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": account.refresh_token,
            "client_id": settings.x_client_id,
        },
        headers={
            "Authorization": f"Basic {auth_value}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )
    data = _raise_for_x_error(response)
    access_token = data.get("access_token")
    if not access_token:
        raise PublishError("X did not return a refreshed access token.")

    account.access_token = access_token
    account.refresh_token = data.get("refresh_token") or account.refresh_token
    account.scopes = data.get("scope") or account.scopes
    if data.get("expires_in"):
        account.token_expires_at = datetime.utcnow() + timedelta(seconds=int(data["expires_in"]))


def publish_to_x(post: Post, account: SocialAccount) -> dict:
    _refresh_access_token(account)
    response = httpx.post(
        "https://api.x.com/2/tweets",
        headers={
            "Authorization": f"Bearer {account.access_token}",
            "Content-Type": "application/json",
        },
        json={"text": _caption(post)},
        timeout=60,
    )
    data = _raise_for_x_error(response)
    tweet_id = (data.get("data") or {}).get("id")
    if not tweet_id:
        raise PublishError("X did not return a post ID.")
    return {
        "external_post_id": tweet_id,
        "platform_post_url": _tweet_url(account, tweet_id),
    }
