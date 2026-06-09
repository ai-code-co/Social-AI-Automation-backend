from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


ROOT_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    openai_api_key: str
    database_url: str
    redis_url: str
    secret_key: str
    debug: bool = True
    image_generation_enabled: bool = True
    image_provider: str = "pollinations"
    pollinations_api_key: str | None = None
    image_model: str = "flux"
    image_size: str = "1024x1024"
    image_enhance: bool = False
    image_safe: bool = True
    public_app_url: str | None = None
    meta_graph_version: str = "v24.0"
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_redirect_uri: str | None = None
    meta_instagram_oauth_enabled: bool = False
    instagram_app_id: str | None = None
    instagram_app_secret: str | None = None
    instagram_redirect_uri: str | None = None
    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    linkedin_redirect_uri: str | None = None
    linkedin_version: str = "202602"
    x_client_id: str | None = None
    x_client_secret: str | None = None
    x_redirect_uri: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value):
        if isinstance(value, str) and value.lower() in {"release", "production", "prod"}:
            return False
        return value

    class Config:
        env_file = ROOT_DIR / ".env"

settings = Settings()
