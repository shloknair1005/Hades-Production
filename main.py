from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api.core.database import engine, Base
from api.core.scheduler import scheduler, register_jobs
from api.routers.all_routers import (
    auth_router, problems_router, runs_router,
    feedback_router, comments_router, shares_router,
    analytics_router, admin_router,
)


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    return FileResponse("static/index.html")

app.mount("/static", StaticFiles(directory="static"), name="static")