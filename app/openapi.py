import json
from typing import Any

from fastapi import FastAPI, Response
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute

from app.api.internal import INTERNAL_TAG


def _remove_422(schema: dict[str, Any]) -> None:
    for method_item in schema.get("paths", {}).values():
        for param in method_item.values():
            param.get("responses", {}).pop("422", None)


def _build_default_schema(app: FastAPI) -> dict[str, Any]:
    routes = [r for r in app.routes if not (isinstance(r, APIRoute) and INTERNAL_TAG in r.tags)]
    tags = [t for t in (app.openapi_tags or []) if t.get("name") != INTERNAL_TAG]
    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        terms_of_service=app.terms_of_service,
        contact=app.contact,
        license_info=app.license_info,
        routes=routes,
        tags=tags,
        servers=app.servers,
    )
    _remove_422(schema)
    return schema


def _build_internal_schema(app: FastAPI) -> dict[str, Any]:
    routes = [r for r in app.routes if isinstance(r, APIRoute) and INTERNAL_TAG in r.tags]
    tags = [t for t in (app.openapi_tags or []) if t.get("name") == INTERNAL_TAG]
    schema = get_openapi(
        title=f"{app.title} - Internal",
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        terms_of_service=app.terms_of_service,
        contact=app.contact,
        license_info=app.license_info,
        routes=routes,
        tags=tags,
        servers=app.servers,
    )
    _remove_422(schema)
    return schema


def setup_openapi(app: FastAPI) -> None:
    """Configure split OpenAPI specs and register internal doc endpoints.

    The default spec (/docs, /openapi.json) excludes Internal-tagged routes.
    The internal spec (/internal/openapi.json, /internal/docs, /internal/redoc)
    contains only Internal-tagged routes.

    Also removes 422 responses replaced by 400 via our exception handler.
    See https://github.com/fastapi/fastapi/discussions/6695
    """
    _internal_schema: dict[str, Any] | None = None

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema  # pragma: no cover
        app.openapi_schema = _build_default_schema(app)
        return app.openapi_schema

    def internal_openapi() -> dict[str, Any]:
        nonlocal _internal_schema
        if _internal_schema is None:
            _internal_schema = _build_internal_schema(app)
        return _internal_schema

    app.openapi = custom_openapi  # ty:ignore[invalid-assignment]

    @app.get("/internal/openapi.json", include_in_schema=False)
    async def internal_openapi_schema() -> Response:
        return Response(content=json.dumps(internal_openapi()), media_type="application/json")

    @app.get("/internal/docs", include_in_schema=False)
    async def internal_docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="/internal/openapi.json", title=f"{app.title} - Internal Docs"
        )

    @app.get("/internal/redoc", include_in_schema=False)
    async def internal_redoc() -> HTMLResponse:
        return get_redoc_html(
            openapi_url="/internal/openapi.json", title=f"{app.title} - Internal Docs"
        )
