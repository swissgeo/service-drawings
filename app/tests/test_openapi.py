from fastapi.testclient import TestClient

from app.api.internal import INTERNAL_TAG


def test_get_openapi_json(client: TestClient):
    response = client.get("openapi.json")

    assert response.status_code == 200, (
        f"Unexpected response {response.status_code}: {response.json()}"
    )
    assert response.headers["content-type"].startswith("application/json")


def test_get_openapi_doc(client: TestClient):
    response = client.get("docs")

    assert response.status_code == 200, (
        f"Unexpected response {response.status_code}: {response.json()}"
    )
    assert response.headers["content-type"].startswith("text/html")


def test_get_openapi_redoc(client: TestClient):
    response = client.get("redoc")

    assert response.status_code == 200, (
        f"Unexpected response {response.status_code}: {response.json()}"
    )
    assert response.headers["content-type"].startswith("text/html")


def test_default_spec_excludes_internal_routes(client: TestClient):
    spec = client.get("openapi.json").json()

    assert "/checker" not in spec["paths"]


def test_default_spec_excludes_internal_tag(client: TestClient):
    spec = client.get("openapi.json").json()

    tag_names = [t["name"] for t in spec.get("tags", [])]
    assert INTERNAL_TAG not in tag_names


def test_internal_openapi_json(client: TestClient):
    response = client.get("internal/openapi.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_internal_spec_contains_checker_route(client: TestClient):
    spec = client.get("internal/openapi.json").json()

    assert "/checker" in spec["paths"]


def test_internal_spec_contains_state_tag(client: TestClient):
    spec = client.get("internal/openapi.json").json()

    tag_names = [t["name"] for t in spec["tags"]]
    assert INTERNAL_TAG in tag_names


def test_internal_docs(client: TestClient):
    response = client.get("internal/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_internal_redoc(client: TestClient):
    response = client.get("internal/redoc")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_default_spec_has_no_422_responses(client: TestClient):
    spec = client.get("openapi.json").json()

    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            assert "422" not in operation.get("responses", {}), (
                f"422 response found at {method.upper()} {path}"
            )


def test_internal_spec_has_no_422_responses(client: TestClient):
    spec = client.get("internal/openapi.json").json()

    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            assert "422" not in operation.get("responses", {}), (
                f"422 response found at {method.upper()} {path}"
            )


def test_public_spec_includes_drawings_routes(client: TestClient):
    """Verify the public OpenAPI spec contains the drawings endpoints."""
    spec = client.get("openapi.json").json()

    assert "/api/wps/v1/drawings" in spec["paths"]
    assert "/api/wps/v1/drawings/{drawing_id}" in spec["paths"]


def test_public_spec_has_post_and_get_for_drawings(client: TestClient):
    """Verify POST and GET methods are documented for drawings routes."""
    spec = client.get("openapi.json").json()

    drawings_post = spec["paths"]["/api/wps/v1/drawings"]
    assert "post" in drawings_post

    drawings_get = spec["paths"]["/api/wps/v1/drawings/{drawing_id}"]
    assert "get" in drawings_get
    assert "put" in drawings_get


def test_public_spec_has_put_error_responses(client: TestClient):
    """Verify the PUT drawing route documents its error responses."""
    spec = client.get("openapi.json").json()

    put_op = spec["paths"]["/api/wps/v1/drawings/{drawing_id}"]["put"]
    for status in ("400", "403", "404"):
        assert status in put_op["responses"]
        schema = put_op["responses"][status]["content"]["application/json"]["schema"]
        assert "$ref" in schema


def test_public_spec_has_post_error_responses(client: TestClient):
    """Verify the POST drawing route documents its error responses."""
    spec = client.get("openapi.json").json()

    post_op = spec["paths"]["/api/wps/v1/drawings"]["post"]
    for status in ("400", "413", "500"):
        assert status in post_op["responses"]
        schema = post_op["responses"][status]["content"]["application/json"]["schema"]
        assert "$ref" in schema


def test_public_spec_has_get_error_responses(client: TestClient):
    """Verify the GET drawing route documents its error responses."""
    spec = client.get("openapi.json").json()

    get_op = spec["paths"]["/api/wps/v1/drawings/{drawing_id}"]["get"]
    for status in ("404", "500"):
        assert status in get_op["responses"]
        schema = get_op["responses"][status]["content"]["application/json"]["schema"]
        assert "$ref" in schema


def test_public_spec_includes_drawings_tag(client: TestClient):
    """Verify the public OpenAPI spec declares the Drawings tag."""
    spec = client.get("openapi.json").json()

    tag_names = [t["name"] for t in spec.get("tags", [])]
    assert "Drawings" in tag_names


# NOTE: we cannot test whether the internal spec is not served when disabled,
# as the application is initialized before the settings are mocked. So this
# that to do this test we would need to change the application and do the
# disabling of spec serving at request time and not at application startup.
# This would require a refactoring of the application and increase its complexity.
# just for testing purposes.
