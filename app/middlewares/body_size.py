"""ASGI middleware for request body size enforcement.

Rejects requests whose Content-Length header exceeds a configured maximum
before the body is read, preventing memory exhaustion from oversized uploads.
"""

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


class MaxBodySizeMiddleware:
    """ASGI middleware that rejects requests exceeding the configured body size limit.

    Checks the Content-Length header before the request body is read, preventing
    memory exhaustion from oversized uploads. Returns 413 Payload Too Large if
    the declared body size exceeds the configured maximum.
    """

    def __init__(self, app: ASGIApp, max_size: int) -> None:
        """Initialize the middleware with the wrapped ASGI app and size limit.

        Args:
            app: The inner ASGI application to wrap.
            max_size: Maximum allowed Content-Length in bytes.

        """
        self.app = app
        self.max_size = max_size
        self._msg = f"Request body exceeds maximum size of {max_size} bytes"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process an ASGI request, rejecting oversized bodies before they are read.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive callable.
            send: The ASGI send callable.

        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for header_name, header_value in scope.get("headers", []):
            if header_name == b"content-length":
                try:
                    if int(header_value) > self.max_size:
                        logger.warning(
                            "Request body of %s bytes exceeds maximum of %s bytes",
                            header_value.decode(),
                            self.max_size,
                        )
                        response = JSONResponse(
                            status_code=413,
                            content={"detail": self._msg},
                        )
                        await response(scope, receive, send)
                        return
                except ValueError:
                    # Malformed Content-Length — let the request proceed
                    logger.debug(
                        "Malformed Content-Length header, skipping body size check: %r",
                        header_value.decode(errors="replace"),
                    )
                break

        await self.app(scope, receive, send)
