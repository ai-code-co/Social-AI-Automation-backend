from datetime import timedelta
from urllib.parse import quote, urljoin

import httpx

from app.auth import create_access_token
from app.config import settings
from app.models.post import Post
from app.models.social_account import SocialAccount
from app.services.image_service import ImageGenerationError, fetch_generated_image
from app.services.post_image_refs import get_image_post_id


class PublishError(Exception):
    pass


def _caption(post: Post) -> str:
    parts = [post.caption.strip()]
    if post.hashtags:
        parts.append(post.hashtags.strip())
    return "\n\n".join(part for part in parts if part)


def _post_image_url(post: Post) -> str | None:
    if not post.image_url:
        return None
    if post.image_url.startswith("http"):
        return post.image_url
    if not settings.public_app_url:
        return None

    image_post_id = get_image_post_id(post)

    image_token = create_access_token(
        f"post_image:{image_post_id}",
        expires_delta=timedelta(hours=6),
    )
    publish_image_path = f"/posts/{image_post_id}/publish-image?token={quote(image_token, safe='')}"
    return urljoin(settings.public_app_url.rstrip("/") + "/", publish_image_path.lstrip("/"))


def _graph_url(path: str) -> str:
    version = settings.meta_graph_version.strip().lstrip("/")
    return f"https://graph.facebook.com/{version}/{path.lstrip('/')}"


def _instagram_graph_url(path: str) -> str:
    version = settings.meta_graph_version.strip().lstrip("/")
    return f"https://graph.instagram.com/{version}/{path.lstrip('/')}"


def _raise_for_graph_error(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise PublishError(f"Meta returned a non-JSON response with status {response.status_code}") from exc

    if response.is_error or "error" in data:
        error = data.get("error", {})
        message = error.get("message") or response.text
        raise PublishError(message)

    return data


def _facebook_post_url(external_id: str | None) -> str | None:
    if not external_id:
        return None
    if "_" in external_id:
        return f"https://www.facebook.com/{external_id}"
    return f"https://www.facebook.com/photo.php?fbid={external_id}"


def publish_to_meta(post: Post, account: SocialAccount) -> dict:
    platform = account.platform.lower()
    if platform == "facebook":
        return _publish_facebook(post, account)
    if platform == "instagram":
        return _publish_instagram(post, account)
    raise PublishError(f"Unsupported Meta platform: {account.platform}")


def _publish_facebook(post: Post, account: SocialAccount) -> dict:
    image_url = _post_image_url(post)
    caption = _caption(post)

    if post.image_url and post.image_prompt:
        endpoint = _graph_url(f"{account.account_id}/photos")
        try:
            image_content, media_type = fetch_generated_image(
                post.image_prompt,
                seed=get_image_post_id(post),
            )
        except ImageGenerationError as exc:
            raise PublishError(f"Could not prepare Facebook image upload: {exc}") from exc

        response = httpx.post(
            endpoint,
            data={
                "caption": caption,
                "access_token": account.access_token,
            },
            files={
                "source": ("post-image.jpg", image_content, media_type),
            },
            timeout=120,
        )
        data = _raise_for_graph_error(response)
        external_id = data.get("post_id") or data.get("id")
        return {
            "external_post_id": external_id,
            "platform_post_url": _facebook_post_url(external_id),
        }

    if image_url:
        endpoint = _graph_url(f"{account.account_id}/photos")
        payload = {
            "url": image_url,
            "caption": caption,
            "access_token": account.access_token,
        }
        try:
            response = httpx.post(endpoint, data=payload, timeout=60)
            data = _raise_for_graph_error(response)
            external_id = data.get("post_id") or data.get("id")
            return {
                "external_post_id": external_id,
                "platform_post_url": _facebook_post_url(external_id),
            }
        except PublishError as exc:
            message = str(exc).lower()
            if "image" not in message and "photo" not in message:
                raise

            raise PublishError(f"Facebook image publish failed: {exc}") from exc

    endpoint = _graph_url(f"{account.account_id}/feed")
    payload = {
        "message": caption,
        "access_token": account.access_token,
    }

    response = httpx.post(endpoint, data=payload, timeout=60)
    data = _raise_for_graph_error(response)
    external_id = data.get("post_id") or data.get("id")
    return {
        "external_post_id": external_id,
        "platform_post_url": _facebook_post_url(external_id),
    }


def _publish_instagram(post: Post, account: SocialAccount) -> dict:
    image_url = _post_image_url(post)
    if not image_url:
        raise PublishError("Instagram publishing requires PUBLIC_APP_URL so the generated image is reachable by Meta.")

    instagram_url = _instagram_graph_url if "instagram_business" in (account.scopes or "") else _graph_url

    container_response = httpx.post(
        instagram_url(f"{account.account_id}/media"),
        data={
            "image_url": image_url,
            "caption": _caption(post),
            "access_token": account.access_token,
        },
        timeout=60,
    )
    container_data = _raise_for_graph_error(container_response)
    creation_id = container_data.get("id")
    if not creation_id:
        raise PublishError("Meta did not return an Instagram media container ID.")

    publish_response = httpx.post(
        instagram_url(f"{account.account_id}/media_publish"),
        data={
            "creation_id": creation_id,
            "access_token": account.access_token,
        },
        timeout=60,
    )
    publish_data = _raise_for_graph_error(publish_response)
    external_id = publish_data.get("id")
    permalink = None
    if external_id:
        permalink_response = httpx.get(
            instagram_url(f"{external_id}"),
            params={
                "fields": "permalink",
                "access_token": account.access_token,
            },
            timeout=30,
        )
        permalink_data = _raise_for_graph_error(permalink_response)
        permalink = permalink_data.get("permalink")
    return {
        "external_post_id": external_id,
        "platform_post_url": permalink,
    }
