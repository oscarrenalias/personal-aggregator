from fastapi import FastAPI

app = FastAPI(
    title="Personal Aggregator API",
    description="JSON REST API for mobile and TUI clients. Read-only access to articles, threads, and feeds.",
)
