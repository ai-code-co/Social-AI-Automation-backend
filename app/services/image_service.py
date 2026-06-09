from urllib.parse import quote

import httpx

from app.config import settings


class ImageGenerationError(Exception):
    pass


def get_post_image_url(post_id: int) -> str | None:
    if not settings.image_generation_enabled:
        return None
    return f"/posts/{post_id}/image"


def _get_image_dimensions() -> tuple[int, int]:
    try:
        width, height = settings.image_size.lower().split("x", maxsplit=1)
        return int(width), int(height)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ImageGenerationError("IMAGE_SIZE must use WIDTHxHEIGHT format, for example 1024x1024.") from exc


def fetch_generated_image(prompt: str, seed: int) -> tuple[bytes, str]:
    if not settings.image_generation_enabled:
        raise ImageGenerationError("Image generation is disabled.")
    image_provider = settings.image_provider.lower()
    if image_provider != "pollinations":
        raise ImageGenerationError(f"Unsupported image provider: {settings.image_provider}")
    if not settings.pollinations_api_key:
        raise ImageGenerationError("POLLINATIONS_API_KEY is required for Pollinations image generation.")
    if not prompt.strip():
        raise ImageGenerationError("This post does not have an image prompt.")

    width, height = _get_image_dimensions()
    image_url = f"https://gen.pollinations.ai/image/{quote(prompt.strip(), safe='')}"
    params = {
        "model": settings.image_model,
        "width": width,
        "height": height,
        "seed": seed,
        "enhance": str(settings.image_enhance).lower(),
        "safe": str(settings.image_safe).lower(),
        "nologo": "true",
        "key": settings.pollinations_api_key,
    }

    try:
        response = httpx.get(image_url, params=params, timeout=120, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ImageGenerationError(f"Image provider request failed: {exc}") from exc

    media_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
    return response.content, media_type
