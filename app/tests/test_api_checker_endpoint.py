from fastapi.testclient import TestClient


def test_api_checker_endpoint(client: TestClient):
    response = client.get("/checker")
    assert response.status_code == 200
