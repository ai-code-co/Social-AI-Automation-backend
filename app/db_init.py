from sqlalchemy import inspect, text
from app.models.base import Base, engine
from app.models import Post, BrandSettings, User


def init_db():
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)

    if inspector.has_table("posts"):
        columns = {column["name"] for column in inspector.get_columns("posts")}
        if "brand_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE posts ADD COLUMN brand_id INTEGER"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_posts_brand_id ON posts (brand_id)"))
        if "image_prompt" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE posts ADD COLUMN image_prompt TEXT"))
        if "image_url" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE posts ADD COLUMN image_url VARCHAR(500)"))
        if "publish_attempts" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE posts ADD COLUMN publish_attempts INTEGER DEFAULT 0"))
        if "external_post_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE posts ADD COLUMN external_post_id VARCHAR(255)"))
        if "platform_post_url" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE posts ADD COLUMN platform_post_url VARCHAR(500)"))
        if "published_at" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE posts ADD COLUMN published_at TIMESTAMP"))
        if "error_log" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE posts ADD COLUMN error_log TEXT"))
        if "status_before_pause" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE posts ADD COLUMN status_before_pause VARCHAR(50)"))

    if inspector.has_table("brand_settings"):
        columns = {column["name"] for column in inspector.get_columns("brand_settings")}
        if "user_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE brand_settings ADD COLUMN user_id INTEGER"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_brand_settings_user_id ON brand_settings (user_id)"))
        if "enabled_platforms" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE brand_settings ADD COLUMN enabled_platforms VARCHAR(255) DEFAULT 'instagram,facebook'")
                )

    if inspector.has_table("users"):
        columns = {column["name"] for column in inspector.get_columns("users")}
        if "google_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE users ADD COLUMN google_id VARCHAR(255)"))
                connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_id ON users (google_id)"))
        if "auth_provider" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE users ADD COLUMN auth_provider VARCHAR(50) DEFAULT 'password' NOT NULL"))

    print("Database tables are ready")


if __name__ == "__main__":
    init_db()
