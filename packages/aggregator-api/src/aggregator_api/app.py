from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aggregator_api.routes.misc import router as misc_router
from aggregator_api.settings import ApiSettings

settings = ApiSettings()

app = FastAPI(
    title="Personal Aggregator API",
    description="JSON REST API for mobile and TUI clients. Read-only access to articles, threads, and feeds.",
)

# Wildcard CORS with no auth is acceptable only behind the network perimeter (Tailscale / 127.0.0.1).
# Tighten allow_origins and add real auth before any public exposure.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.api_cors_allow_origins.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(misc_router, prefix="/api/v1")
