import httpx

from app.config import settings
from app.models.post import Post
from app.models.social_account import SocialAccount
from app.services.image_service import ImageGenerationError, fetch_generated_image


class PublishError(Exception):
    pass


def _caption(post: Post) -> str:
    parts = [post.caption.strip()]
    if post.hashtags:
        parts.append(post.hashtags.strip())
    return "\n\n".join(part for part in parts if part)


def _author_urn(account: SocialAccount) -> str:
    account_id = account.account_id.strip()
    if account_id.startswith("urn:li:person:") or account_id.startswith("urn:li:organization:"):
        return account_id
    return f"urn:li:person:{account_id}"


def _headers(account: SocialAccount, content_type: str = "application/json") -> dict:
    return {
        "Authorization": f"Bearer {account.access_token}",
        "Content-Type": content_type,
        "Linkedin-Version": settings.linkedin_version,
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _raise_for_linkedin_error(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.is_error:
        message = data.get("message") or data.get("error_description") or data.get("error") or response.text
        raise PublishError(message or "LinkedIn request failed")

    return data


def _post_url(post_urn: str | None) -> str | None:
    if not post_urn:
        return None
    return f"https://www.linkedin.com/feed/update/{post_urn}"


def _upload_image(post: Post, account: SocialAccount, author: str) -> str | None:
    if not post.image_prompt:
        return None

    try:
        image_content, media_type = fetch_generated_image(post.image_prompt, seed=post.id)
    except ImageGenerationError:
        return None

    initialize_response = httpx.post(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        headers=_headers(account),
        json={"initializeUploadRequest": {"owner": author}},
        timeout=60,
    )
    initialize_data = _raise_for_linkedin_error(initialize_response)
    value = initialize_data.get("value") or {}
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")
    if not upload_url or not image_urn:
        raise PublishError("LinkedIn did not return an image upload URL.")

    upload_response = httpx.put(
        upload_url,
        headers={"Authorization": f"Bearer {account.access_token}", "Content-Type": media_type},
        content=image_content,
        timeout=120,
    )
    if upload_response.is_error:
        raise PublishError(upload_response.text or "LinkedIn image upload failed")

    return image_urn


def publish_to_linkedin(post: Post, account: SocialAccount) -> dict:
    author = _author_urn(account)
    payload = {
        "author": author,
        "commentary": _caption(post),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    image_urn = _upload_image(post, account, author)
    if image_urn:
        payload["content"] = {"media": {"id": image_urn}}

    response = httpx.post(
        "https://api.linkedin.com/rest/posts",
        headers=_headers(account),
        json=payload,
        timeout=60,
    )
    _raise_for_linkedin_error(response)
    external_id = response.headers.get("x-restli-id")
    return {
        "external_post_id": external_id,
        "platform_post_url": _post_url(external_id),
    }
