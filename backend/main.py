"""
Relay — FastAPI application entry point.

Startup: initialises DB tables, seeds answer bank.
Routers registered: answer_bank, pending (Phase 3).
Phases 4–8 will add more routers here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import AsyncSessionLocal, init_db
from backend.routers import answer_bank as answer_bank_router
from backend.services.scheduler import start_scheduler, stop_scheduler
from backend.routers import applications as applications_router
from backend.routers import automation as automation_router
from backend.routers import pending as pending_router
from backend.routers import queue as queue_router
from backend.utils.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("relay_startup")
    await init_db()
    async with AsyncSessionLocal() as session:
        from backend.services.answer_bank import seed_answer_bank
        seeded = await seed_answer_bank(session)
        if seeded:
            logger.info("answer_bank_seeded_on_startup", extra={"count": seeded})
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("relay_shutdown")


app = FastAPI(
    title="Relay",
    description="Autonomous job application agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(answer_bank_router.router)
app.include_router(pending_router.router)
app.include_router(applications_router.router)
app.include_router(queue_router.router)
app.include_router(automation_router.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "relay"}
