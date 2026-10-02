"""FastAPI application for synchronizing Kodi playback progress."""

from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator


DEFAULT_DB_PATH = "/data/watchsync.db"


def _db_path() -> Path:
    return Path(os.getenv("WATCHSYNC_DB", DEFAULT_DB_PATH))


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_db_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _initialize_database() -> None:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS progress (
                media_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                media_type TEXT NOT NULL,
                show_title TEXT,
                position_seconds REAL NOT NULL,
                duration_seconds REAL,
                season INTEGER,
                episode INTEGER,
                year INTEGER,
                source_path TEXT,
                thumbnail TEXT,
                device_id TEXT,
                completed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_progress_updated_at "
            "ON progress(updated_at DESC)"
        )


def _authorize(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("WATCHSYNC_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WATCHSYNC_TOKEN is not configured",
        )
    if authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


class ProgressUpsert(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    media_key: str = Field(min_length=1, max_length=512)
    title: str = Field(default="", max_length=512)
    media_type: Literal["movie", "episode", "video", "unknown"] = "unknown"
    show_title: str | None = Field(default=None, max_length=512)
    position_seconds: float = Field(ge=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=0)
    year: int | None = Field(default=None, ge=1800, le=3000)
    source_path: str | None = Field(default=None, max_length=4096)
    thumbnail: str | None = Field(default=None, max_length=4096)
    device_id: str | None = Field(default=None, max_length=256)
    completed: bool = False

    @field_validator(
        "show_title", "source_path", "thumbnail", "device_id", mode="after"
    )
    @classmethod
    def empty_optional_strings_are_none(cls, value: str | None) -> str | None:
        return value or None


class ProgressRecord(ProgressUpsert):
    updated_at: datetime


class ProgressList(BaseModel):
    items: list[ProgressRecord]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


def _row_to_record(row: sqlite3.Row) -> ProgressRecord:
    return ProgressRecord.model_validate(dict(row))


@asynccontextmanager
async def lifespan(_: FastAPI):
    _initialize_database()
    yield


def create_app() -> FastAPI:
    api = FastAPI(
        title="WatchSync Server",
        version="0.1.0",
        lifespan=lifespan,
    )

    @api.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @api.post(
        "/v1/progress",
        response_model=ProgressRecord,
        dependencies=[Depends(_authorize)],
    )
    def upsert_progress(payload: ProgressUpsert) -> ProgressRecord:
        updated_at = datetime.now(timezone.utc).isoformat()
        values = payload.model_dump()
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO progress (
                    media_key, title, media_type, show_title, position_seconds,
                    duration_seconds, season, episode, year, source_path,
                    thumbnail, device_id, completed, updated_at
                ) VALUES (
                    :media_key, :title, :media_type, :show_title, :position_seconds,
                    :duration_seconds, :season, :episode, :year, :source_path,
                    :thumbnail, :device_id, :completed, :updated_at
                )
                ON CONFLICT(media_key) DO UPDATE SET
                    title = excluded.title,
                    media_type = excluded.media_type,
                    show_title = excluded.show_title,
                    position_seconds = excluded.position_seconds,
                    duration_seconds = excluded.duration_seconds,
                    season = excluded.season,
                    episode = excluded.episode,
                    year = excluded.year,
                    source_path = excluded.source_path,
                    thumbnail = excluded.thumbnail,
                    device_id = excluded.device_id,
                    completed = excluded.completed,
                    updated_at = excluded.updated_at
                """,
                {**values, "updated_at": updated_at},
            )
            row = connection.execute(
                "SELECT * FROM progress WHERE media_key = ?", (payload.media_key,)
            ).fetchone()
        assert row is not None
        return _row_to_record(row)

    @api.get(
        "/v1/progress",
        response_model=ProgressList,
        dependencies=[Depends(_authorize)],
    )
    def list_progress(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        unfinished: bool = False,
    ) -> ProgressList:
        where = "WHERE completed = 0" if unfinished else ""
        with _connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM progress {where} ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return ProgressList(items=[_row_to_record(row) for row in rows])

    @api.get(
        "/v1/progress/{media_key:path}",
        response_model=ProgressRecord,
        dependencies=[Depends(_authorize)],
    )
    def get_progress(media_key: str) -> ProgressRecord:
        with _connect() as connection:
            row = connection.execute(
                "SELECT * FROM progress WHERE media_key = ?", (media_key,)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Progress not found")
        return _row_to_record(row)

    return api


app = create_app()
