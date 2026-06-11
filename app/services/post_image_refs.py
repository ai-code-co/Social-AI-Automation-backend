import re

from app.models.post import Post


POST_IMAGE_PATH_PATTERN = re.compile(r"/posts/(\d+)/(?:image|publish-image)")


def get_image_post_id(post: Post) -> int:
    if post.image_url:
        match = POST_IMAGE_PATH_PATTERN.search(post.image_url)
        if match:
            return int(match.group(1))
    return post.id
