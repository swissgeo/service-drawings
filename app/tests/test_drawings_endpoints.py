"""Integration tests for the drawings API endpoints.

Tests cover the full request/response cycle for creating, retrieving, and
updating KMZ drawing files via the FastAPI TestClient with a mocked S3 backend.
"""

import hashlib
import io
import logging
import uuid
import zipfile
from datetime import datetime

from fastapi.testclient import TestClient

import pytest

from app.core.drawings import DrawingsService, get_drawings_service
from app.core.exceptions import S3Error


def _auth(admin_id: object) -> dict[str, str]:
    """Return the Authorization header carrying the given admin_id as a bearer token."""
    return {"Authorization": f"Bearer {admin_id}"}


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
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"test.kmz\"; filename*=UTF-8''test.kmz"
    )
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
        data={"sha256": _sha256(new_content)},
        headers=_auth(admin_id),
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
    """PUT with a mismatched admin_id returns 403 and leaves the drawing untouched."""
    drawing_id, _ = _create(client, valid_kmz_bytes)

    new_content = _build_kmz("<kml><Document><Placemark/></Document></kml>")
    response = client.put(
        f"/api/wps/v1/drawings/{drawing_id}",
        files={"file": ("test.kmz", new_content, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(new_content)},
        headers=_auth(uuid.uuid4()),
    )
    assert response.status_code == 403
    assert "detail" in response.json()
    assert client.get(f"/api/wps/v1/drawings/{drawing_id}").content == valid_kmz_bytes


def test_update_drawing_not_found(client: TestClient, valid_kmz_bytes: bytes):
    """PUT a non-existent drawing returns 404 Not Found."""
    response = client.put(
        "/api/wps/v1/drawings/00000000-0000-0000-0000-000000000000",
        files={"file": ("test.kmz", valid_kmz_bytes, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(valid_kmz_bytes)},
        headers=_auth(str(uuid.uuid4())),
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
        data={"sha256": _sha256(valid_kmz_bytes)},
        headers=_auth(admin_id),
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
        data={"sha256": "0" * 64},
        headers=_auth(admin_id),
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
        headers=_auth(admin_id),
    )
    assert response.status_code == 204

    # The drawing must no longer be retrievable
    get_resp = client.get(f"/api/wps/v1/drawings/{drawing_id}")
    assert get_resp.status_code == 404


def test_delete_drawing_wrong_admin_id(client: TestClient, valid_kmz_bytes: bytes):
    """DELETE with a mismatched admin_id returns 403 and does not delete."""
    drawing_id, _ = _create(client, valid_kmz_bytes)

    response = client.delete(f"/api/wps/v1/drawings/{drawing_id}", headers=_auth(uuid.uuid4()))
    assert response.status_code == 403
    assert "detail" in response.json()
    assert client.get(f"/api/wps/v1/drawings/{drawing_id}").status_code == 200


def test_delete_drawing_not_found(client: TestClient):
    """DELETE a non-existent drawing returns 404 Not Found."""
    response = client.request(
        "DELETE",
        "/api/wps/v1/drawings/00000000-0000-0000-0000-000000000000",
        headers=_auth(str(uuid.uuid4())),
    )
    assert response.status_code == 404
    assert "detail" in response.json()


def test_delete_drawing_missing_authorization(client: TestClient, valid_kmz_bytes: bytes):
    """DELETE without an Authorization header returns 401 and does not delete."""
    drawing_id, _ = _create(client, valid_kmz_bytes)

    response = client.delete(f"/api/wps/v1/drawings/{drawing_id}")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.get(f"/api/wps/v1/drawings/{drawing_id}").status_code == 200


def test_delete_drawing_admin_id_in_body_is_ignored(client: TestClient, valid_kmz_bytes: bytes):
    """The admin_id is no longer read from the form body, only from the header."""
    drawing_id, admin_id = _create(client, valid_kmz_bytes)

    response = client.request(
        "DELETE", f"/api/wps/v1/drawings/{drawing_id}", data={"admin_id": admin_id}
    )
    assert response.status_code == 401


def test_delete_drawing_invalid_uuid(client: TestClient):
    """DELETE with a malformed UUID returns 422 Unprocessable Entity."""
    response = client.request(
        "DELETE",
        "/api/wps/v1/drawings/not-a-valid-uuid",
        headers=_auth(str(uuid.uuid4())),
    )
    assert response.status_code == 422


def _create(client: TestClient, content: bytes, filename: str = "test.kmz") -> tuple[str, str]:
    """Upload a drawing and return its (drawing_id, admin_id)."""
    resp = client.post(
        "/api/wps/v1/drawings",
        files={"file": (filename, content, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(content)},
    )
    assert resp.status_code == 201
    return resp.json()["id"], resp.json()["admin_id"]


def test_create_drawing_records_original_filename(
    client: TestClient, valid_kmz_bytes: bytes, settings, s3_client
):
    """POST stores the client-supplied filename as S3 metadata."""
    drawing_id, _ = _create(client, valid_kmz_bytes, filename="France.kmz")

    head = s3_client.head_object(
        Bucket=settings.aws_s3_bucket_name, Key=f"drawings/{drawing_id}.kmz"
    )
    assert head["Metadata"]["original-filename"] == "France.kmz"


def test_check_auth_matching_pair(client: TestClient, valid_kmz_bytes: bytes):
    """GET check-auth with the correct admin_id answers 204 with an empty body."""
    drawing_id, admin_id = _create(client, valid_kmz_bytes)

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/check-auth", headers=_auth(admin_id))
    assert response.status_code == 204
    assert response.content == b""


def test_check_auth_wrong_admin_id(client: TestClient, valid_kmz_bytes: bytes):
    """GET check-auth on an existing drawing with a mismatched admin_id answers 403."""
    drawing_id, _ = _create(client, valid_kmz_bytes)

    response = client.get(
        f"/api/wps/v1/drawings/{drawing_id}/check-auth", headers=_auth(uuid.uuid4())
    )
    assert response.status_code == 403
    assert "detail" in response.json()


def test_check_auth_unknown_drawing(client: TestClient):
    """GET check-auth on a drawing that does not exist answers 404."""
    response = client.get(
        "/api/wps/v1/drawings/00000000-0000-0000-0000-000000000000/check-auth",
        headers=_auth(uuid.uuid4()),
    )
    assert response.status_code == 404
    assert "detail" in response.json()


def test_check_auth_after_delete(client: TestClient, valid_kmz_bytes: bytes):
    """A previously valid pair answers 404 once the drawing is deleted."""
    drawing_id, admin_id = _create(client, valid_kmz_bytes)
    url = f"/api/wps/v1/drawings/{drawing_id}/check-auth"
    assert client.get(url, headers=_auth(admin_id)).status_code == 204

    delete_resp = client.delete(f"/api/wps/v1/drawings/{drawing_id}", headers=_auth(admin_id))
    assert delete_resp.status_code == 204

    assert client.get(url, headers=_auth(admin_id)).status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not-a-uuid"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
    ],
    ids=["absent", "empty", "no-token", "not-a-uuid", "wrong-scheme"],
)
def test_check_auth_missing_or_malformed_authorization(
    client: TestClient, valid_kmz_bytes: bytes, headers: dict[str, str]
):
    """A missing or unusable Authorization header answers 401 with a Bearer challenge."""
    drawing_id, _ = _create(client, valid_kmz_bytes)

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/check-auth", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "detail" in response.json()


def test_check_auth_scheme_is_case_insensitive(client: TestClient, valid_kmz_bytes: bytes):
    """The auth scheme is case-insensitive per RFC 9110 section 11.1."""
    drawing_id, admin_id = _create(client, valid_kmz_bytes)

    response = client.get(
        f"/api/wps/v1/drawings/{drawing_id}/check-auth",
        headers={"Authorization": f"bearer {admin_id}"},
    )
    assert response.status_code == 204


def test_check_auth_answers_are_not_cacheable(client: TestClient, valid_kmz_bytes: bytes):
    """Every answer that depends on the Authorization header is marked no-store.

    Otherwise a cache keyed on the URL alone could replay one caller's 204 to a
    caller with a wrong admin_id, or a 403 to one with the right admin_id.
    """
    drawing_id, admin_id = _create(client, valid_kmz_bytes)
    url = f"/api/wps/v1/drawings/{drawing_id}/check-auth"

    for headers, status in ((_auth(admin_id), 204), (_auth(uuid.uuid4()), 403), ({}, 401)):
        response = client.get(url, headers=headers)
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store, max-age=0"


@pytest.mark.parametrize(
    ("headers", "expected_log"),
    [
        ({}, "Missing or non-Bearer Authorization header"),
        ({"Authorization": "Basic c2VjcmV0LXRva2Vu"}, "Missing or non-Bearer Authorization header"),
        ({"Authorization": "Bearer secret-token"}, "Bearer token is not a UUID"),
    ],
    ids=["absent", "wrong-scheme", "not-a-uuid"],
)
def test_unusable_authorization_is_logged_without_token(
    client: TestClient,
    valid_kmz_bytes: bytes,
    caplog: pytest.LogCaptureFixture,
    headers: dict[str, str],
    expected_log: str,
):
    """A rejected Authorization header is logged at info level, never its value."""
    drawing_id, _ = _create(client, valid_kmz_bytes)

    with caplog.at_level(logging.INFO, logger="app.api.wps"):
        response = client.get(f"/api/wps/v1/drawings/{drawing_id}/check-auth", headers=headers)

    assert response.status_code == 401
    records = [r for r in caplog.records if r.name == "app.api.wps"]
    assert [r.levelno for r in records] == [logging.INFO]
    assert expected_log in records[0].getMessage()
    assert f"/api/wps/v1/drawings/{drawing_id}/check-auth" in records[0].getMessage()
    assert "secret" not in caplog.text
    assert "c2VjcmV0LXRva2Vu" not in caplog.text


def test_check_auth_invalid_uuid(client: TestClient):
    """GET check-auth with a malformed drawing UUID returns 422."""
    response = client.get(
        "/api/wps/v1/drawings/not-a-valid-uuid/check-auth", headers=_auth(uuid.uuid4())
    )
    assert response.status_code == 422


def test_metadata_success(client: TestClient, valid_kmz_bytes: bytes):
    """GET metadata returns the original filename and the timestamps."""
    drawing_id, _ = _create(client, valid_kmz_bytes, filename="France.kmz")

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == drawing_id
    assert data["original_filename"] == "France.kmz"
    assert datetime.fromisoformat(data["created_at"]) == datetime.fromisoformat(data["modified_at"])


def test_metadata_reflects_update(client: TestClient, valid_kmz_bytes: bytes):
    """After an update, metadata reports the new filename and a later modified_at."""
    drawing_id, admin_id = _create(client, valid_kmz_bytes, filename="before.kmz")
    before = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata").json()

    new_content = _build_kmz("<kml><Document><Placemark/></Document></kml>")
    update_resp = client.put(
        f"/api/wps/v1/drawings/{drawing_id}",
        files={"file": ("after.kmz", new_content, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(new_content)},
        headers=_auth(admin_id),
    )
    assert update_resp.status_code == 200

    after = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata").json()
    assert after["original_filename"] == "after.kmz"
    # created_at is preserved across the update, modified_at moves forward
    assert after["created_at"] == before["created_at"]
    assert datetime.fromisoformat(after["modified_at"]) >= datetime.fromisoformat(
        before["modified_at"]
    )


def test_metadata_unicode_filename_roundtrip(client: TestClient, valid_kmz_bytes: bytes):
    """A non-ASCII filename survives the S3 metadata round-trip.

    S3 user metadata travels in HTTP headers and must be ASCII, so the name is
    percent-encoded on the way in and decoded on the way out.
    """
    drawing_id, _ = _create(client, valid_kmz_bytes, filename="Zürich Höhenweg.kmz")

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata")
    assert response.status_code == 200
    assert response.json()["original_filename"] == "Zürich Höhenweg.kmz"


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("../../../etc/passwd.kmz", "passwd.kmz"),
        ("/home/user/maps/France.kmz", "France.kmz"),
        (r"C:\Users\bob\France.kmz", "France.kmz"),
    ],
)
def test_metadata_strips_path_components(
    client: TestClient, valid_kmz_bytes: bytes, sent: str, expected: str
):
    """Directory components in the client filename are stripped before storage."""
    drawing_id, _ = _create(client, valid_kmz_bytes, filename=sent)

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata")
    assert response.status_code == 200
    assert response.json()["original_filename"] == expected


def test_metadata_long_filename_is_truncated(client: TestClient, valid_kmz_bytes: bytes):
    """An over-long filename is shortened before its extension, which is kept."""
    drawing_id, _ = _create(client, valid_kmz_bytes, filename="a" * 2000 + ".kmz")

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata")
    assert response.status_code == 200
    expected_stem = "a" * (DrawingsService.MAX_ENCODED_FILENAME_LENGTH - len(".kmz"))
    assert response.json()["original_filename"] == expected_stem + ".kmz"


# S3 rejects user metadata over 2 KB (keys and values, UTF-8), but moto does not
# enforce the limit, so it is checked on the stored object instead.
S3_USER_METADATA_LIMIT_BYTES = 2048


@pytest.mark.parametrize(
    "stem",
    ["a" * 2000, "中" * 400, "😀" * 255, "Zürich " * 200],
    ids=["ascii", "cjk", "emoji", "mixed"],
)
def test_long_filename_fits_s3_metadata_limit(
    client: TestClient, valid_kmz_bytes: bytes, settings, s3_client, stem: str
):
    """However long or multi-byte the name, the stored metadata stays within 2 KB.

    The cap applies to the percent-encoded form: each non-ASCII UTF-8 byte
    becomes three characters, so capping the raw character count would let 255
    emoji encode to over 3 KB.
    """
    drawing_id, _ = _create(client, valid_kmz_bytes, filename=stem + ".kmz")

    metadata = s3_client.head_object(
        Bucket=settings.aws_s3_bucket_name, Key=f"drawings/{drawing_id}.kmz"
    )["Metadata"]
    assert len(metadata["original-filename"]) <= DrawingsService.MAX_ENCODED_FILENAME_LENGTH
    total = sum(len(k.encode()) + len(v.encode()) for k, v in metadata.items())
    assert total <= S3_USER_METADATA_LIMIT_BYTES

    name = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata").json()["original_filename"]
    assert name.endswith(".kmz")
    assert stem.startswith(name.removesuffix(".kmz"))


def test_long_filename_with_overlong_extension_is_cut_as_a_whole():
    """A "suffix" too long to be a real extension is not preserved."""
    encoded = DrawingsService.encode_filename("x." + "b" * 2000, uuid.uuid4())
    assert encoded == "x." + "b" * (DrawingsService.MAX_ENCODED_FILENAME_LENGTH - 2)


def test_metadata_legacy_object_without_filename(client: TestClient, settings, s3_client):
    """A drawing stored before filenames were recorded reports a null filename."""
    drawing_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    s3_client.put_object(
        Bucket=settings.aws_s3_bucket_name,
        Key=f"drawings/{drawing_id}.kmz",
        Body=b"PK\x03\x04",
        Metadata={
            "sha256": "0" * 64,
            "admin-id": str(admin_id),
            "created-at": "2026-01-01T12:00:00+00:00",
            "modified-at": "2026-01-02T12:00:00+00:00",
        },
    )

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata")
    assert response.status_code == 200
    data = response.json()
    assert data["original_filename"] is None
    assert data["created_at"] == "2026-01-01T12:00:00Z"


def test_metadata_unusable_timestamps(client: TestClient, settings, s3_client):
    """Corrupt stored timestamps surface as a sanitized 500 rather than a crash."""
    drawing_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    s3_client.put_object(
        Bucket=settings.aws_s3_bucket_name,
        Key=f"drawings/{drawing_id}.kmz",
        Body=b"PK\x03\x04",
        Metadata={"admin-id": str(admin_id), "created-at": "not-a-timestamp"},
    )

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata")
    assert response.status_code == 500
    assert "not-a-timestamp" not in response.json()["detail"]


def test_metadata_not_found(client: TestClient):
    """GET metadata for a non-existent drawing returns 404 Not Found."""
    response = client.get("/api/wps/v1/drawings/00000000-0000-0000-0000-000000000000/metadata")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_metadata_needs_no_admin_id(client: TestClient, valid_kmz_bytes: bytes):
    """GET metadata is public, like the download that already exposes the filename."""
    drawing_id, _ = _create(client, valid_kmz_bytes, filename="France.kmz")

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata")
    assert response.status_code == 200
    assert response.json()["original_filename"] == "France.kmz"
    # A wrong admin_id is simply ignored rather than rejected
    response = client.get(
        f"/api/wps/v1/drawings/{drawing_id}/metadata", headers=_auth(uuid.uuid4())
    )
    assert response.status_code == 200


def test_metadata_is_not_cacheable(client: TestClient, valid_kmz_bytes: bytes):
    """GET metadata is marked no-store, since an update changes the answer."""
    drawing_id, _ = _create(client, valid_kmz_bytes)

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata")
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_metadata_invalid_uuid(client: TestClient):
    """GET metadata with a malformed drawing UUID returns 422."""
    response = client.get("/api/wps/v1/drawings/not-a-valid-uuid/metadata")
    assert response.status_code == 422


@pytest.mark.parametrize("sent", ["/", "..", "../", "."])
def test_metadata_path_only_filename_falls_back(
    client: TestClient, valid_kmz_bytes: bytes, sent: str
):
    """A filename that is purely path syntax falls back to "{drawing_id}.kmz".

    Such a name leaves no usable basename behind, and storing it verbatim would
    hand clients back a filename they cannot write to disk.
    """
    drawing_id, _ = _create(client, valid_kmz_bytes, filename=sent)

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}/metadata")
    assert response.status_code == 200
    assert response.json()["original_filename"] == f"{drawing_id}.kmz"


def test_get_drawing_content_disposition_uses_original_filename(
    client: TestClient, valid_kmz_bytes: bytes
):
    """GET offers the download under the name the client uploaded it with."""
    drawing_id, _ = _create(client, valid_kmz_bytes, filename="France.kmz")

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"France.kmz\"; filename*=UTF-8''France.kmz"
    )


def test_get_drawing_content_disposition_unicode_filename(
    client: TestClient, valid_kmz_bytes: bytes
):
    """A non-ASCII name is carried exactly in filename* and degraded in filename.

    The bare filename parameter cannot hold non-ASCII, so it only serves clients
    that ignore filename*; those characters are replaced rather than dropped so
    the fallback stays a plausible filename.
    """
    drawing_id, _ = _create(client, valid_kmz_bytes, filename="Zürich.kmz")

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"Z_rich.kmz\"; filename*=UTF-8''Z%C3%BCrich.kmz"
    )


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ('ev"il.kmz', "attachment; filename=\"ev_il.kmz\"; filename*=UTF-8''ev%22il.kmz"),
        (
            r"back\slash.kmz",
            "attachment; filename=\"back_slash.kmz\"; filename*=UTF-8''back%5Cslash.kmz",
        ),
        (
            "new\nline.kmz",
            "attachment; filename=\"new_line.kmz\"; filename*=UTF-8''new%0Aline.kmz",
        ),
    ],
)
def test_build_content_disposition_neutralises_header_breakers(filename: str, expected: str):
    """Quotes, backslashes and control characters cannot break out of the header.

    This is tested directly rather than through an upload because the multipart
    layer percent-encodes such characters before the service ever sees them; the
    guarantee has to hold for whatever reaches the service.
    """
    assert DrawingsService.build_content_disposition(filename) == expected


def test_get_drawing_content_disposition_after_update(client: TestClient, valid_kmz_bytes: bytes):
    """The download name follows the filename of the most recent update."""
    drawing_id, admin_id = _create(client, valid_kmz_bytes, filename="before.kmz")

    new_content = _build_kmz("<kml><Document><Placemark/></Document></kml>")
    update_resp = client.put(
        f"/api/wps/v1/drawings/{drawing_id}",
        files={"file": ("after.kmz", new_content, "application/vnd.google-earth.kmz")},
        data={"sha256": _sha256(new_content)},
        headers=_auth(admin_id),
    )
    assert update_resp.status_code == 200

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}")
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"after.kmz\"; filename*=UTF-8''after.kmz"
    )


def test_get_drawing_content_disposition_legacy_object(client: TestClient, settings, s3_client):
    """A drawing stored before filenames were recorded downloads as {drawing_id}.kmz."""
    drawing_id = uuid.uuid4()
    s3_client.put_object(
        Bucket=settings.aws_s3_bucket_name,
        Key=f"drawings/{drawing_id}.kmz",
        Body=b"PK\x03\x04",
        Metadata={
            "sha256": "0" * 64,
            "admin-id": str(uuid.uuid4()),
            "created-at": "2026-01-01T12:00:00+00:00",
            "modified-at": "2026-01-01T12:00:00+00:00",
        },
    )

    response = client.get(f"/api/wps/v1/drawings/{drawing_id}")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f"attachment; filename=\"{drawing_id}.kmz\"; filename*=UTF-8''{drawing_id}.kmz"
    )
