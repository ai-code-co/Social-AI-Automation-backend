from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import settings
from app.db_init import init_db
from app.routes.auth import router as auth_router
from app.routes.posts import router as posts_router
from app.routes.brand import router as brand_router
from app.routes.social_accounts import router as social_accounts_router
from app.services.scheduler import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()

app = FastAPI(
    title="Social AI Automation",
    version="1.0.0",
    lifespan=lifespan
)

cors_origins = [
    origin.strip()
    for origin in settings.cors_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(posts_router)
app.include_router(brand_router)
app.include_router(social_accounts_router)

@app.get("/")
def health_check():
    return {"status": "running", "app": "Social AI Automation"}
