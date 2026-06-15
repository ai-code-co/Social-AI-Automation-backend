import httpx

from app.config import settings
from app.models.post import Post
from app.models.social_account import SocialAccount


class MetricsSyncError(Exception):
    pass


def _graph_url(path: str) -> str:
    version = settings.meta_graph_version.strip().lstrip("/")
    return f"https://graph.facebook.com/{version}/{path.lstrip('/')}"


def _instagram_graph_url(path: str) -> str:
    version = settings.meta_graph_version.strip().lstrip("/")
    return f"https://graph.instagram.com/{version}/{path.lstrip('/')}"


def _raise_for_meta_error(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise MetricsSyncError(f"Meta returned a non-JSON response with status {response.status_code}") from exc

    if response.is_error or "error" in data:
        error = data.get("error", {})
        if isinstance(error, dict):
            message = error.get("message") or response.text
        else:
            message = str(error) or response.text
        raise MetricsSyncError(message)

    return data


def _insight_value(data: dict, metric_name: str) -> int:
    for item in data.get("data", []):
        if item.get("name") != metric_name:
            continue
        values = item.get("values") or []
        if not values:
            return 0
        value = values[-1].get("value", 0)
        if isinstance(value, dict):
            return sum(int(count or 0) for count in value.values())
        return int(value or 0)
    return 0


def _is_invalid_metric_error(exc: MetricsSyncError) -> bool:
    message = str(exc).lower()
    return "valid insights metric" in message or ("(#100)" in message and "metric" in message)


def _facebook_insight_value(post: Post, account: SocialAccount, metric_names: list[str]) -> int:
    for metric_name in metric_names:
        try:
            insights_response = httpx.get(
                _graph_url(f"{post.external_post_id}/insights"),
                params={
                    "metric": metric_name,
                    "access_token": account.access_token,
                },
                timeout=30,
            )
            insights_data = _raise_for_meta_error(insights_response)
            return _insight_value(insights_data, metric_name)
        except MetricsSyncError as exc:
            if _is_invalid_metric_error(exc):
                continue
            raise
    return 0


def sync_meta_post_metrics(post: Post, account: SocialAccount) -> dict:
    platform = account.platform.lower()
    if platform == "facebook":
        return _sync_facebook_metrics(post, account)
    if platform == "instagram":
        return _sync_instagram_metrics(post, account)
    raise MetricsSyncError(f"Unsupported Meta metrics platform: {account.platform}")


def _sync_facebook_metrics(post: Post, account: SocialAccount) -> dict:
    if not post.external_post_id:
        raise MetricsSyncError("Post does not have a Facebook post ID.")

    reactions_count = 0
    comments_count = 0
    shares_count = 0

    try:
        object_response = httpx.get(
            _graph_url(post.external_post_id),
            params={
                "fields": "shares,comments.summary(true).limit(0),reactions.summary(true).limit(0)",
                "access_token": account.access_token,
            },
            timeout=30,
        )
        object_data = _raise_for_meta_error(object_response)
        reactions_count = int(((object_data.get("reactions") or {}).get("summary") or {}).get("total_count") or 0)
        comments_count = int(((object_data.get("comments") or {}).get("summary") or {}).get("total_count") or 0)
        shares_count = int((object_data.get("shares") or {}).get("count") or 0)
    except MetricsSyncError as exc:
        # If we don't have pages_read_engagement permission, still try to get insights
        if "pages_read_engagement" not in str(exc):
            raise

    views_count = 0
    clicks_count = 0

    views_count = _facebook_insight_value(
        post,
        account,
        ["post_impressions", "post_impressions_unique", "post_engaged_users"],
    )
    clicks_count = _facebook_insight_value(
        post,
        account,
        ["post_clicks", "post_clicks_unique"],
    )

    return {
        "views_count": views_count,
        "likes_count": reactions_count,
        "comments_count": comments_count,
        "shares_count": shares_count,
        "clicks_count": clicks_count,
    }


def _sync_instagram_metrics(post: Post, account: SocialAccount) -> dict:
    if not post.external_post_id:
        raise MetricsSyncError("Post does not have an Instagram media ID.")

    instagram_url = _instagram_graph_url if "instagram_business" in (account.scopes or "") else _graph_url
    media_response = httpx.get(
        instagram_url(post.external_post_id),
        params={
            "fields": "like_count,comments_count",
            "access_token": account.access_token,
        },
        timeout=30,
    )
    media_data = _raise_for_meta_error(media_response)

    likes_count = int(media_data.get("like_count") or 0)
    comments_count = int(media_data.get("comments_count") or 0)
    views_count = 0

    for metrics in ["views,reach", "impressions,reach"]:
        try:
            insights_response = httpx.get(
                instagram_url(f"{post.external_post_id}/insights"),
                params={
                    "metric": metrics,
                    "access_token": account.access_token,
                },
                timeout=30,
            )
            insights_data = _raise_for_meta_error(insights_response)
            views_count = (
                _insight_value(insights_data, "views")
                or _insight_value(insights_data, "impressions")
                or _insight_value(insights_data, "reach")
            )
            break
        except MetricsSyncError:
            continue

    return {
        "views_count": views_count,
        "likes_count": likes_count,
        "comments_count": comments_count,
        "shares_count": 0,
        "clicks_count": 0,
    }
