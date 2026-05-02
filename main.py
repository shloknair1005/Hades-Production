from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.core.database import engine, Base
from api.core.scheduler import scheduler, register_jobs
from api.middleware.payload_guard import PayloadGuardMiddleware
from api.middleware.rate_limiter import RateLimiterMiddleware
from api.middleware.request_logger import RequestLoggerMiddleware
from api.middleware.error_handler import register_error_handlers
from api.routers.all_routers import (
    auth_router, problems_router, runs_router,
    feedback_router, comments_router, shares_router,
    analytics_router, admin_router,
)
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "Hades-Production", "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    register_jobs()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    await engine.dispose()


app = FastAPI(title="Judges of Hades", lifespan=lifespan)

# ── Middleware stack (applied bottom-up by Starlette) ─────────────────────────
# Execution order on request:  PayloadGuard → RateLimiter → RequestLogger → handler
# Execution order on response: handler → RequestLogger → RateLimiter → PayloadGuard
app.add_middleware(RequestLoggerMiddleware)   # outermost — sees final status code
app.add_middleware(RateLimiterMiddleware)
app.add_middleware(PayloadGuardMiddleware)    # innermost — runs first on request
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Exception handlers ────────────────────────────────────────────────────────
register_error_handlers(app)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(problems_router)
app.include_router(runs_router)
app.include_router(feedback_router)
app.include_router(comments_router)
app.include_router(shares_router)
app.include_router(analytics_router)
app.include_router(admin_router)


@app.get("/")
async def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")