"""Tests for the GET /syntheticz synthetic check endpoint."""

from unittest.mock import AsyncMock, MagicMock

from mypy_boto3_s3 import S3Client

from fastapi.testclient import TestClient

from app.core.s3 import S3Service
from app.core.syntheticz_service import SyntheticzService, get_syntheticz_service
from app.settings import Settings
from app.version import __version__


def test_api_syntheticz_endpoint_up(client: TestClient):
    """GET /syntheticz returns 200 with UP status when the S3 bucket is reachable."""
    response = client.get("/syntheticz")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "service": {"name": "service-drawings", "version": __version__},
        "status": "UP",
        "external_systems": {"s3": {"status": "UP"}},
    }


def test_api_syntheticz_endpoint_bucket_missing(
    client: TestClient, s3_client: S3Client, settings: Settings
):
    """GET /syntheticz returns 500 with DOWN status when the S3 bucket does not exist."""
    s3_client.delete_bucket(Bucket=settings.aws_s3_bucket_name)

    response = client.get("/syntheticz")
    assert response.status_code == 500
    assert response.headers["Cache-Control"] == "no-store"
    data = response.json()
    assert data["status"] == "DOWN"
    assert data["external_systems"] == {"s3": {"status": "DOWN"}}


def test_api_syntheticz_endpoint_s3_exception(client: TestClient, settings: Settings):
    """GET /syntheticz returns 500 with DOWN status when the S3 check raises."""
    broken_s3 = S3Service(client=MagicMock(), bucket=settings.aws_s3_bucket_name)
    broken_s3.check_bucket = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    client.app.dependency_overrides[get_syntheticz_service] = lambda: SyntheticzService(broken_s3)  # type: ignore  # noqa: PGH003

    try:
        response = client.get("/syntheticz")
        assert response.status_code == 500
        assert response.headers["Cache-Control"] == "no-store"
        data = response.json()
        assert data["service"] == {"name": "service-drawings", "version": __version__}
        assert data["status"] == "DOWN"
        assert data["external_systems"] == {"s3": {"status": "DOWN"}}
    finally:
        del client.app.dependency_overrides[get_syntheticz_service]  # type: ignore  # noqa: PGH003
