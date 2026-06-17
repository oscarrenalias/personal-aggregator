from fastapi import FastAPI

from aggregator_api.routes.misc import router as misc_router

app = FastAPI(
    title="Personal Aggregator API",
    description="JSON REST API for mobile and TUI clients. Read-only access to articles, threads, and feeds.",
)

app.include_router(misc_router, prefix="/api/v1")
