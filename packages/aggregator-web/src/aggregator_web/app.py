from pathlib import Path
from typing import Generator

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_common.db import SessionFactory, get_session
from aggregator_common.version import version

_BASE_DIR = Path(__file__).parent

app = FastAPI(title="personal-aggregator web")

app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=_BASE_DIR / "templates")


def get_db() -> Generator[Session, None, None]:
    db = SessionFactory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/healthz")
def healthz() -> JSONResponse:
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    status_code = 200 if db_status == "ok" else 500
    return JSONResponse(
        status_code=status_code,
        content={"version": version(), "db": db_status},
    )
