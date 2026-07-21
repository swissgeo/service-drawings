from fastapi.testclient import TestClient


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
