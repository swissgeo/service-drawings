"""Split public and internal OpenAPI specification generation.

Produces two separate OpenAPI schemas: a public one excluding Internal-tagged
routes and an internal one containing only those routes. Registers dedicated
Swagger UI and ReDoc endpoints for the internal spec. Strips 422 responses
replaced by app-level 400 exception handlers.
"""

import json
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, Response, routing
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute

from app.api.internal import INTERNAL_TAG
from app.settings import get_settings

_INTERNAL_SPEC_PREFIX = "internal"
_INTERNAL_SPEC_URL = f"/{_INTERNAL_SPEC_PREFIX}/openapi.json"
_SPEC_URL = "/openapi.json"


def _iter_routes(app: FastAPI) -> list[Any]:
    """Return the app routes flattened, one entry per operation.

    Since FastAPI 0.141 / Starlette 1.6, ``include_router`` stores a single lazy
    ``_IncludedRouter`` in ``app.routes`` instead of flattening the child routes,
    so iterating ``app.routes`` directly no longer yields the individual
    ``APIRoute`` objects. ``iter_route_contexts`` resolves those into contexts
    that expose the effective path and tags (router prefix and tags merged in),
    and ``get_openapi`` accepts them in place of routes. Older FastAPI versions
    allowed by our version constraint lack that helper but already flatten the
    routes, so fall back to plain iteration there.
    """
    iter_route_contexts = getattr(routing, "iter_route_contexts", None)
    if iter_route_contexts is None:  # pragma: no cover - FastAPI < 0.141
        return list(app.routes)
    return list(iter_route_contexts(app.routes))


def _is_internal(route: Any) -> bool:
    """Return whether a route (or route context) is tagged as internal."""
    original = getattr(route, "original_route", route)
    return isinstance(original, APIRoute) and INTERNAL_TAG in (getattr(route, "tags", None) or [])


def _remove_422(schema: dict[str, Any]) -> None:
    for method_item in schema.get("paths", {}).values():
        for param in method_item.values():
            param.get("responses", {}).pop("422", None)


def _build_default_schema(app: FastAPI) -> dict[str, Any]:
    routes = [r for r in _iter_routes(app) if not _is_internal(r)]
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
    routes = [r for r in _iter_routes(app) if _is_internal(r)]
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
        if _internal_schema is None:  # pragma: no cover
            _internal_schema = _build_internal_schema(app)
        return _internal_schema

    app.openapi = custom_openapi  # ty:ignore[invalid-assignment]

    @app.get(_INTERNAL_SPEC_URL, include_in_schema=False)
    async def internal_openapi_schema() -> Response:
        return Response(content=json.dumps(internal_openapi()), media_type="application/json")

    @app.get(f"/{_INTERNAL_SPEC_PREFIX}/docs", include_in_schema=False)
    async def internal_docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=_INTERNAL_SPEC_URL, title=f"{app.title} - Internal Docs"
        )

    @app.get(f"/{_INTERNAL_SPEC_PREFIX}/redoc", include_in_schema=False)
    async def internal_redoc() -> HTMLResponse:
        return get_redoc_html(openapi_url=_INTERNAL_SPEC_URL, title=f"{app.title} - Internal Docs")


@lru_cache
def get_openapi_spec_url() -> str | None:
    """Return the OpenAPI spec URL if publishing is enabled, otherwise None."""
    if get_settings().publish_openapi_spec:
        return _SPEC_URL
    return None  # pragma: no cover
