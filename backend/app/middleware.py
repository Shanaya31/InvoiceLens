
"""ASGI middleware for limiting aggregate document-upload request bodies."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

ASGIMessage = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]


class RequestBodyLimitMiddleware:
    """Limit POST /api/documents bodies without buffering the entire request."""

    def __init__(self, app: Any, max_body_bytes: int) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive.")

        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/documents"
        ):
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                value
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-length"
            ),
            None,
        )

        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = None

            if declared_length is not None and (
                declared_length < 0 or declared_length > self.max_body_bytes
            ):
                await self._send_too_large(send)
                return

        bytes_received = 0
        rejected = False

        async def limited_receive() -> ASGIMessage:
            nonlocal bytes_received, rejected

            if rejected:
                return {"type": "http.disconnect"}

            message = await receive()

            if message["type"] == "http.request":
                bytes_received += len(message.get("body", b""))

                if bytes_received > self.max_body_bytes:
                    rejected = True
                    await self._send_too_large(send)
                    return {"type": "http.disconnect"}

            return message

        async def guarded_send(message: ASGIMessage) -> None:
            # The 413 response has already been sent if the body was too large.
            if not rejected:
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not rejected:
                raise

    async def _send_too_large(self, send: Send) -> None:
        body = json.dumps(
            {"detail": "Upload request exceeds the configured size limit."}
        ).encode("utf-8")

        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
