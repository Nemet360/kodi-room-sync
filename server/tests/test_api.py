from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


TOKEN = "test-secret"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WATCHSYNC_TOKEN", TOKEN)
    monkeypatch.setenv("WATCHSYNC_DB", str(tmp_path / "watchsync.db"))
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def sample_payload(**overrides):
    payload = {
        "media_key": "movie:tt0133093",
        "title": "The Matrix",
        "media_type": "movie",
        "year": 1999,
        "position_seconds": 1234.5,
        "duration_seconds": 8160,
        "source_path": "smb://media/movies/The Matrix.mkv",
        "thumbnail": "https://example.test/matrix.jpg",
        "device_id": "living-room",
    }
    payload.update(overrides)
    return payload


def test_health_does_not_require_authentication(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "authorization", [None, "Bearer wrong", "Basic test-secret", TOKEN]
)
def test_progress_endpoints_reject_invalid_auth(
    client: TestClient, authorization: str | None
):
    headers = {"Authorization": authorization} if authorization else {}

    response = client.get("/v1/progress", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_unconfigured_token_returns_service_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("WATCHSYNC_TOKEN", raising=False)
    monkeypatch.setenv("WATCHSYNC_DB", str(tmp_path / "watchsync.db"))
    with TestClient(create_app()) as client:
        response = client.get("/v1/progress")

    assert response.status_code == 503


def test_create_and_fetch_progress(client: TestClient, headers: dict[str, str]):
    created = client.post("/v1/progress", json=sample_payload(), headers=headers)

    assert created.status_code == 200
    body = created.json()
    assert body["media_key"] == "movie:tt0133093"
    assert body["position_seconds"] == 1234.5
    assert body["updated_at"].endswith(("Z", "+00:00"))

    fetched = client.get("/v1/progress/movie:tt0133093", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_upsert_replaces_existing_progress(
    client: TestClient, headers: dict[str, str]
):
    client.post("/v1/progress", json=sample_payload(), headers=headers)
    updated = client.post(
        "/v1/progress",
        json=sample_payload(position_seconds=2222, device_id="bedroom"),
        headers=headers,
    )

    assert updated.status_code == 200
    assert updated.json()["position_seconds"] == 2222
    assert updated.json()["device_id"] == "bedroom"
    listing = client.get("/v1/progress", headers=headers).json()
    assert len(listing["items"]) == 1


def test_list_is_newest_first_and_honors_limit(
    client: TestClient, headers: dict[str, str]
):
    client.post(
        "/v1/progress",
        json=sample_payload(media_key="first", title="First"),
        headers=headers,
    )
    client.post(
        "/v1/progress",
        json=sample_payload(media_key="second", title="Second"),
        headers=headers,
    )

    response = client.get("/v1/progress?limit=1", headers=headers)

    assert response.status_code == 200
    assert [item["media_key"] for item in response.json()["items"]] == ["second"]


def test_unfinished_filter_excludes_completed_items(
    client: TestClient, headers: dict[str, str]
):
    client.post(
        "/v1/progress",
        json=sample_payload(media_key="finished", completed=True),
        headers=headers,
    )
    client.post(
        "/v1/progress",
        json=sample_payload(media_key="unfinished", completed=False),
        headers=headers,
    )

    response = client.get("/v1/progress?unfinished=true", headers=headers)

    assert response.status_code == 200
    assert [item["media_key"] for item in response.json()["items"]] == ["unfinished"]


def test_media_key_can_contain_path_segments(
    client: TestClient, headers: dict[str, str]
):
    client.post(
        "/v1/progress",
        json=sample_payload(media_key="plugin://example/item/42"),
        headers=headers,
    )

    response = client.get(
        "/v1/progress/plugin://example/item/42", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["media_key"] == "plugin://example/item/42"


def test_missing_progress_returns_404(client: TestClient, headers: dict[str, str]):
    response = client.get("/v1/progress/does-not-exist", headers=headers)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"media_key": ""},
        {"position_seconds": -1},
        {"duration_seconds": 0},
        {"media_type": "music"},
        {"season": -1},
        {"year": 1700},
    ],
)
def test_invalid_payload_is_rejected(
    client: TestClient, headers: dict[str, str], overrides: dict
):
    response = client.post(
        "/v1/progress", json=sample_payload(**overrides), headers=headers
    )

    assert response.status_code == 422


def test_list_limit_is_validated(client: TestClient, headers: dict[str, str]):
    assert client.get("/v1/progress?limit=0", headers=headers).status_code == 422
    assert client.get("/v1/progress?limit=101", headers=headers).status_code == 422
