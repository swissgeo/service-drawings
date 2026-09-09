"""Integration tests for the drawings API endpoints.

Tests cover the full request/response cycle for creating, retrieving, and
updating KMZ drawing files via the FastAPI TestClient with a mocked S3 backend.
"""

import hashlib
import io
import uuid
import zipfile
from datetime import datetime

from fastapi.testclient import TestClient

import pytest

from app.core.drawings import DrawingsService, get_drawings_service
from app.core.exceptions import S3Error


def _sha256(data: bytes) -> str:
    """Return the SHA-256 hex digest of the given bytes."""
    return hashlib.sha256(data).hexdigest()


def _build_kmz(kml_content: str) -> bytes:
    """Build an in-memory KMZ (ZIP containing doc.kml) with the given KML content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml_content)
    return buf.getvalue()


@pytest.fixture
def valid_kmz_bytes() -> bytes:
    """Create a valid KMZ file (ZIP containing doc.kml) in memory."""
    return _build_kmz(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>'
    )


@pytest.fixture
def invalid_bytes() -> bytes:
    """Return bytes that are NOT a valid ZIP archive."""
    return b"this is not a kmz file"


@pytest.fixture
def oversized_bytes() -> bytes:
    """Return bytes exceeding the default 5 MB limit."""
    return b"\x00" * (5 * 1024 * 1024 + 1)


def test_create_drawing_valid_kmz(client: TestClient, valid_kmz_bytes: bytes):
    """POST a valid KMZ file returns 201 with id, admin_id, and s3_url."""
    response = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert "admin_id" in data
    assert "s3_url" in data
    # s3_url should point to the GET drawing endpoint on the same domain
    assert data["s3_url"].startswith("http://testserver/api/wps/v1/drawings/")
    assert data["id"] in data["s3_url"]


def test_create_drawing_digest_mismatch(client: TestClient, valid_kmz_bytes: bytes):
    """POST with a wrong SHA-256 returns 400 Bad Request."""
    response = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": "0" * 64},
    )
    assert response.status_code == 400
    assert "detail" in response.json()


def test_create_drawing_uppercase_digest(client: TestClient, valid_kmz_bytes: bytes):
    """POST with a correct SHA-256 in uppercase hex is accepted."""
    response = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes).upper()},
    )
    assert response.status_code == 201


def test_create_drawing_invalid_kmz(client: TestClient, invalid_bytes: bytes):
    """POST a non-ZIP file returns 400 Bad Request."""
    response = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", invalid_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(invalid_bytes)},
    )
    assert response.status_code == 400
    assert "detail" in response.json()


def test_create_drawing_oversized_kmz(client: TestClient, oversized_bytes: bytes):
    """POST a file exceeding 5 MB returns 413 Payload Too Large."""
    response = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("large.kmz", oversized_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(oversized_bytes)},
    )
    assert response.status_code == 413
    assert "detail" in response.json()


def test_get_drawing_existing(client: TestClient, valid_kmz_bytes: bytes):
    """GET an existing drawing returns 200 with the KMZ content."""
    # First upload a drawing
    create_resp = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
    )
    assert create_resp.status_code == 201
    drawing_id = create_resp.json()["id"]

    # Then retrieve it
    response = client.get(f"/api/wps/v1/drawings/{drawing_id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.google-earth.kmz"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.content == valid_kmz_bytes


def test_get_drawing_not_found(client: TestClient):
    """GET a non-existent drawing returns 404 Not Found."""
    response = client.get("/api/wps/v1/drawings/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_update_drawing_success(client: TestClient, valid_kmz_bytes: bytes):
    """PUT an existing drawing with valid content returns 200 and replaces it."""
    create_resp = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
    )
    assert create_resp.status_code == 201
    drawing_id = create_resp.json()["id"]
    admin_id = create_resp.json()["admin_id"]

    new_content = _build_kmz(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark/></Document></kml>'
    )
    response = client.put(
        f"/api/wps/v1/drawings/{drawing_id}",
        files={"file": ("test.kmz", new_content, "application/vnd.google-earth.kmz")},
        data={"admin_id": admin_id, "sha256": _sha256(new_content)},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == drawing_id
    assert data["admin_id"] == admin_id
    assert data["s3_url"].startswith("http://testserver/api/wps/v1/drawings/")
    assert "created_at" in data
    assert "modified_at" in data
    assert datetime.fromisoformat(data["modified_at"]) >= datetime.fromisoformat(data["created_at"])

    # The stored content must have been replaced
    get_resp = client.get(f"/api/wps/v1/drawings/{drawing_id}")
    assert get_resp.status_code == 200
    assert get_resp.content == new_content


def test_update_drawing_wrong_admin_id(client: TestClient, valid_kmz_bytes: bytes):
    """PUT with a mismatched admin_id returns 403 Forbidden."""
    create_resp = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
    )
    assert create_resp.status_code == 201
    drawing_id = create_resp.json()["id"]

    response = client.put(
        f"/api/wps/v1/drawings/{drawing_id}",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"admin_id": str(uuid.uuid4()), "sha256": _sha256(valid_kmz_bytes)},
    )
    assert response.status_code == 403
    assert "detail" in response.json()


def test_update_drawing_not_found(client: TestClient, valid_kmz_bytes: bytes):
    """PUT a non-existent drawing returns 404 Not Found."""
    response = client.put(
        "/api/wps/v1/drawings/00000000-0000-0000-0000-000000000000",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"admin_id": str(uuid.uuid4()), "sha256": _sha256(valid_kmz_bytes)},
    )
    assert response.status_code == 404
    assert "detail" in response.json()


def test_update_drawing_unchanged_content(
    client: TestClient, valid_kmz_bytes: bytes, settings, s3_client
):
    """PUT with identical content returns 200 without re-uploading to S3."""
    create_resp = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
    )
    assert create_resp.status_code == 201
    drawing_id = create_resp.json()["id"]
    admin_id = create_resp.json()["admin_id"]

    # Capture the stored metadata and last-modified before the update
    key = f"drawings/{drawing_id}.kmz"
    before = s3_client.head_object(Bucket=settings.aws_s3_bucket_name, Key=key)
    before_last_modified = before["LastModified"]
    before_metadata = before["Metadata"]

    response = client.put(
        f"/api/wps/v1/drawings/{drawing_id}",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"admin_id": admin_id, "sha256": _sha256(valid_kmz_bytes)},
    )
    assert response.status_code == 200

    # No re-upload happened: the object is untouched
    after = s3_client.head_object(Bucket=settings.aws_s3_bucket_name, Key=key)
    assert after["LastModified"] == before_last_modified
    assert after["Metadata"] == before_metadata


def test_update_drawing_digest_mismatch(client: TestClient, valid_kmz_bytes: bytes):
    """PUT with a wrong SHA-256 returns 400 Bad Request."""
    create_resp = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
    )
    assert create_resp.status_code == 201
    drawing_id = create_resp.json()["id"]
    admin_id = create_resp.json()["admin_id"]

    response = client.put(
        f"/api/wps/v1/drawings/{drawing_id}",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"admin_id": admin_id, "sha256": "0" * 64},
    )
    assert response.status_code == 400
    assert "detail" in response.json()


def test_get_drawing_invalid_uuid(client: TestClient):
    """GET with a malformed UUID returns 422 Unprocessable Entity."""
    response = client.get("/api/wps/v1/drawings/not-a-valid-uuid")
    assert response.status_code == 422


def test_create_drawing_s3_failure(client: TestClient, valid_kmz_bytes: bytes, settings):
    """POST when S3 upload fails returns 500 with sanitized message."""
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    from app.core.s3 import S3Service  # noqa: PLC0415

    broken_s3 = S3Service(
        client=MagicMock(),
        bucket=settings.aws_s3_bucket_name,
    )
    broken_s3.upload_drawing = AsyncMock(side_effect=S3Error("AWS error details here"))

    broken_drawings = DrawingsService(s3=broken_s3)

    client.app.dependency_overrides[get_drawings_service] = lambda: broken_drawings  # type: ignore  # noqa: PGH003

    try:
        response = client.post(
            "/api/wps/v1/drawings",
            files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
            data={"sha256": _sha256(valid_kmz_bytes)},
        )
        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        # The message should be sanitized, NOT contain AWS details
        assert "AWS error details" not in data["detail"]
    finally:
        del client.app.dependency_overrides[get_drawings_service]  # type: ignore  # noqa: PGH003


def test_create_drawing_body_size_exceeded(client: TestClient):
    """POST with Content-Length exceeding max_upload_size_bytes returns 413."""
    response = client.post(
        "/api/wps/v1/drawings",
        content=b"x" * 100,
        headers={"Content-Length": str(11_000_000)},  # exceeds 5 MB default
    )
    assert response.status_code == 413
    assert "detail" in response.json()


def test_create_drawing_body_size_within_limit(client: TestClient, valid_kmz_bytes: bytes):
    """POST with Content-Length within limit succeeds normally."""
    response = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
    )
    assert response.status_code == 201


def test_delete_drawing_success(client: TestClient, valid_kmz_bytes: bytes):
    """DELETE an existing drawing with the correct admin_id returns 204 and removes it."""
    create_resp = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
    )
    assert create_resp.status_code == 201
    drawing_id = create_resp.json()["id"]
    admin_id = create_resp.json()["admin_id"]

    response = client.request(
        "DELETE",
        f"/api/wps/v1/drawings/{drawing_id}",
        data={"admin_id": admin_id},
    )
    assert response.status_code == 204

    # The drawing must no longer be retrievable
    get_resp = client.get(f"/api/wps/v1/drawings/{drawing_id}")
    assert get_resp.status_code == 404


def test_delete_drawing_wrong_admin_id(client: TestClient, valid_kmz_bytes: bytes):
    """DELETE with a mismatched admin_id returns 403 Forbidden."""
    create_resp = client.post(
        "/api/wps/v1/drawings",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
    )
    assert create_resp.status_code == 201
    drawing_id = create_resp.json()["id"]

    response = client.request(
        "DELETE",
        f"/api/wps/v1/drawings/{drawing_id}",
        data={"admin_id": str(uuid.uuid4())},
    )
    assert response.status_code == 403
    assert "detail" in response.json()


def test_delete_drawing_not_found(client: TestClient):
    """DELETE a non-existent drawing returns 404 Not Found."""
    response = client.request(
        "DELETE",
        "/api/wps/v1/drawings/00000000-0000-0000-0000-000000000000",
        data={"admin_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404
    assert "detail" in response.json()


def test_delete_drawing_missing_admin_id(client: TestClient):
    """DELETE without the admin_id form field returns 422 Unprocessable Entity."""
    response = client.request("DELETE", "/api/wps/v1/drawings/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 422


def test_delete_drawing_invalid_uuid(client: TestClient):
    """DELETE with a malformed UUID returns 422 Unprocessable Entity."""
    response = client.request(
        "DELETE",
        "/api/wps/v1/drawings/not-a-valid-uuid",
        data={"admin_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422
