from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.s3 import S3Service, get_s3_service


def test_api_checker_endpoint(client: TestClient):
    response = client.get("/checker")
    assert response.status_code == 200


def test_api_checker_ready_endpoint(client: TestClient):
    """GET /checker/ready returns 200 with success=true when S3 is reachable."""
    response = client.get("/checker/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "OK"
    assert "version" in data


def test_api_checker_ready_unavailable(client: TestClient, settings):
    """GET /checker/ready returns 503 when S3 is unreachable."""
    from unittest.mock import MagicMock  # noqa: PLC0415
    broken_s3 = S3Service(
        client=MagicMock(),
        bucket=settings.aws_s3_bucket_name,
    )
    broken_s3.check_bucket = AsyncMock(return_value=False)  # type: ignore  # noqa: PGH003

    client.app.dependency_overrides[get_s3_service] = lambda: broken_s3  # type: ignore  # noqa: PGH003

    try:
        response = client.get("/checker/ready")
        assert response.status_code == 503
    finally:
        del client.app.dependency_overrides[get_s3_service]  # type: ignore  # noqa: PGH003
