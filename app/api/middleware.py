"""HTTP observability middleware.

For every request: assign (or accept) an ``X-Request-ID``, log method,
path, status and duration, and bump aggregate counters. Errors raised by
handlers are logged with the request id already bound.
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core import metrics
from app.core.context import request_id_var

logger = logging.getLogger("app.http")

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_PATH_LENGTH = 200


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Request id, access log with duration, and request/error counters."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.monotonic() - started) * 1000
            metrics.inc_counter("http_requests_total")
            metrics.inc_counter("http_errors_total")
            logger.exception(
                "http_request method=%s path=%s status=500 "
                "duration_ms=%.1f request_id=%s",
                request.method,
                self._short_path(request),
                duration_ms,
                request_id,
            )
            request_id_var.reset(token)
            raise
        duration_ms = (time.monotonic() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        status_code = response.status_code
        metrics.inc_counter("http_requests_total")
        if status_code >= 500:
            metrics.inc_counter("http_errors_total")
        logger.info(
            "http_request method=%s path=%s status=%s duration_ms=%.1f "
            "request_id=%s",
            request.method,
            self._short_path(request),
            status_code,
            duration_ms,
            request_id,
        )
        request_id_var.reset(token)
        return response

    @staticmethod
    def _short_path(request: Request) -> str:
        path = request.url.path
        return path[:_MAX_PATH_LENGTH]
