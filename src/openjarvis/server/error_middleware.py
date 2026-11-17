"""Answer unhandled exceptions with a JSON 500 that browser clients can read."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class JSONErrorMiddleware:
    """Send the 500 for an unhandled exception from inside ``CORSMiddleware``.

    Starlette answers an unhandled exception from ``ServerErrorMiddleware``,
    which always wraps every user middleware. That reply never passes through
    ``CORSMiddleware``, so it has no ``Access-Control-Allow-Origin`` header,
    and a cross-origin caller such as the desktop app's webview
    (``http://tauri.localhost``) cannot read it: ``fetch`` rejects with
    "Failed to fetch" and the caller cannot tell a crash from a server that
    is not running.

    Registered directly inside ``CORSMiddleware``, this middleware sends the
    500 itself, as JSON, so it gets the CORS headers. It then re-raises, as
    ``ServerErrorMiddleware`` does, so the server still logs the traceback
    and test clients still see the exception. Exceptions after the response
    has started (a broken stream) are only re-raised.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if not response_started:
                response = JSONResponse(
                    {
                        "detail": (
                            f"Internal server error ({type(exc).__name__}). "
                            "The server log has the details."
                        )
                    },
                    status_code=500,
                )
                await response(scope, receive, send)
            raise


__all__ = ["JSONErrorMiddleware"]
