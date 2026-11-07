"""Private OpenAI-compatible transport for the existing real model adapters."""
import argparse
import asyncio
import hmac
import json
import sys

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from .config import RuntimeConfig
from .preflight import check
from .requests import validate
from .runner import RuntimeFailure, ModelRunner


def error(code, status):
    return JSONResponse({"error": {"code": code}}, status_code=status)


class PrivateGate:
    def __init__(self, app, owner, token, limit):
        self.app, self.owner, self.token, self.limit = app, owner, token, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        if not hmac.compare_digest(headers.get(b"authorization", b""), b"Bearer " + self.token):
            return await error("AUTH_REQUIRED", 401)(scope, receive, send)
        is_inference = scope["method"] == "POST"
        if is_inference and (self.owner.state.busy or self.owner.state.runner.faulted):
            return await error("RUNTIME_UNAVAILABLE", 503)(scope, receive, send)
        if is_inference:
            self.owner.state.busy = True
        try:
            body = bytearray()
            async with asyncio.timeout(30):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > self.limit:
                        return await error("BODY_TOO_LARGE", 413)(scope, receive, send)
                    if not message.get("more_body", False):
                        break
            consumed = False
            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
            await self.app(scope, replay, send)
        except TimeoutError:
            await error("REQUEST_TIMEOUT", 408)(scope, receive, send)
        finally:
            if is_inference:
                self.owner.state.busy = False


def create_app(config, runner=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.busy = False
    app.state.runner = runner if runner is not None else ModelRunner(config)
    app.add_middleware(PrivateGate, owner=app, token=config.token().encode("ascii"), limit=config.max_body_bytes)

    @app.get("/healthz")
    async def health():
        # Liveness is deliberately not a claim that upstream or inference works.
        return {"status": "FAULTED" if app.state.runner.faulted else "RUNNING",
                "busy": app.state.busy, "inference_verified": False}

    async def execute(kind, value):
        try:
            return JSONResponse(await app.state.runner.run(kind, value))
        except RuntimeFailure as exc:
            return error(exc.code, exc.status)

    @app.post("/v1/chat/completions")
    @app.post("/v1/embeddings")
    @app.post("/v1/rerank")
    async def structured(request: Request):
        kind = {"/v1/chat/completions": "chat", "/v1/embeddings": "embedding", "/v1/rerank": "reranker"}[request.url.path]
        try:
            value = validate(kind, await request.json(), config.chat_model)
        except (ValueError, TypeError, KeyError, RecursionError):
            return error("INVALID_INPUT", 422)
        return await execute(kind, value)

    @app.post("/v1/audio/transcriptions")
    async def transcribe(request: Request):
        try:
            async with request.form(max_files=1, max_fields=1) as form:
                if len(form.multi_items()) != 2 or set(form) != {"model", "file"} or form["model"] != "Qwen3-ASR-0.6B":
                    raise ValueError()
                upload = form["file"]
                if upload.content_type not in ("audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3"):
                    raise ValueError()
                value = await upload.read(10 * 1024 * 1024 + 1)
                if not 0 < len(value) <= 10 * 1024 * 1024:
                    raise ValueError()
        except (ValueError, TypeError, AttributeError):
            return error("INVALID_INPUT", 422)
        return await execute("asr", value)

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        config = RuntimeConfig.read(args.config)
        check(config)
    except (ValueError, OSError, KeyError):
        print("SELFHOST_PREFLIGHT_FAILED", file=sys.stderr)
        return 1
    import uvicorn
    uvicorn.run(create_app(config), host=config.bind, port=config.port, workers=1,
                access_log=False, log_level="warning", timeout_graceful_shutdown=15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
