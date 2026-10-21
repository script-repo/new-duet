"""HTTP: REST + SSE under /api (SPEC §10), the built PWA at /. One process, one port."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from tandem import __version__
from tandem.connectors.base import PushRequest
from tandem.connectors.simple import WebhookAuthError
from tandem.runtime import Runtime
from tandem.server.routes_connections import connections_router
from tandem.server.routes_evals import evals_router
from tandem.server.routes_push import push_router

log = logging.getLogger("tandem.server")
STATIC_DIR = Path(__file__).parent / "static"
COOKIE = "tandem_token"

# ---- bodies ------------------------------------------------------------------------------------------------


class Login(BaseModel):
    token: str


class ThreadPost(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    harness: str | None = None


class DecisionBody(BaseModel):
    decision: Literal["approve", "reject", "edit"]
    args: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=2000)


class TaskBody(BaseModel):
    instructions: str = Field(min_length=1, max_length=20000)
    title: str | None = None
    harness: str | None = None


class SteerBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class MemoryBody(BaseModel):
    kind: Literal["core", "preference", "fact", "note"] = "fact"
    content: str = Field(min_length=1, max_length=4000)
    key: str | None = None


def create_app(rt: Runtime | None = None, run_scheduler: bool | None = None) -> FastAPI:
    runtime = rt or Runtime()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await runtime.start(
            run_scheduler=runtime.settings.autostart if run_scheduler is None else run_scheduler
        )
        yield
        await runtime.stop()

    app = FastAPI(
        title="Tandem",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.rt = runtime
    if runtime.settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in runtime.settings.cors_origins.split(",")],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def auth(request: Request) -> None:
        token = runtime.settings.auth_token
        if not token:
            return
        expected = token.get_secret_value()
        given = request.cookies.get(COOKIE) or ""
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            given = header[7:]
        if not (given and hmac.compare_digest(given, expected)):
            raise HTTPException(401, "sign in required")

    def r404(obj: Any, what: str) -> Any:
        if obj is None:
            raise HTTPException(404, f"{what} not found")
        return obj

    api = Depends(auth)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "mode": "mock" if runtime.cfg.mock else "live"}

    @app.post("/api/auth/login")
    async def login(body: Login, response: Response) -> dict[str, Any]:
        token = runtime.settings.auth_token
        if token and not hmac.compare_digest(body.token, token.get_secret_value()):
            raise HTTPException(401, "wrong token")
        response.set_cookie(COOKIE, body.token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 30)
        return {"ok": True}

    @app.get("/api/config", dependencies=[api])
    async def config() -> dict[str, Any]:
        return runtime.public_config()

    # ---- thread ---------------------------------------------------------------------------------------------
    def _refs(messages: list[dict[str, Any]]) -> dict[str, Any]:
        s = runtime.store
        out: dict[str, dict[str, Any]] = {
            "approvals": {},
            "tasks": {},
            "artifacts": {},
            "briefs": {},
            "events": {},
        }
        for m in messages:
            card = m.get("card") or {}
            ref = card.get("ref")
            if not ref:
                continue
            kind = card.get("type")
            if kind == "approval":
                obj = s.get_approval(ref)
                if obj:
                    out["approvals"][ref] = obj
            elif kind == "task":
                obj = s.get_task(ref)
                if obj:
                    out["tasks"][ref] = obj
                    for aid in obj.get("artifact_ids", []):
                        if art := s.get_artifact(aid):
                            out["artifacts"][aid] = art
            elif kind == "artifact":
                obj = s.get_artifact(ref)
                if obj:
                    out["artifacts"][ref] = obj
            elif kind == "brief":
                obj = s.get_brief(ref)
                if obj:
                    out["briefs"][ref] = obj
                    for item in obj.get("items", []):
                        if item.get("approval_id"):
                            ap = s.get_approval(item["approval_id"])
                            if ap:
                                out["approvals"][ap["id"]] = ap
            elif kind == "notification":
                obj = s.get_event(ref)
                if obj:
                    out["events"][ref] = obj
        return out

    @app.get("/api/thread", dependencies=[api])
    async def thread(limit: int = 50, before: int | None = None) -> dict[str, Any]:
        msgs = runtime.store.list_messages(limit=min(limit, 200), before=before)
        return {"messages": msgs, **_refs(msgs)}

    @app.post("/api/thread", dependencies=[api])
    async def post_thread(body: ThreadPost) -> dict[str, Any]:
        msg = runtime.store.add_message("user", body.text)
        runtime.background(runtime.chat.reply(body.text, body.harness))
        return {"message": msg}

    @app.get("/api/stream", dependencies=[api])
    async def stream(request: Request) -> EventSourceResponse:
        q = runtime.bus.subscribe()

        async def gen() -> AsyncIterator[dict[str, str]]:
            try:
                yield {
                    "event": "hello",
                    "data": json.dumps({"server_time": runtime.store.now(), "version": __version__}),
                }
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                    except TimeoutError:
                        continue
                    yield {"event": ev.type, "data": json.dumps(ev.data, default=str)}
            finally:
                runtime.bus.unsubscribe(q)

        return EventSourceResponse(gen(), ping=15)

    # ---- approvals ------------------------------------------------------------------------------------------
    @app.get("/api/approvals", dependencies=[api])
    async def approvals(state: str | None = None) -> list[dict[str, Any]]:
        return runtime.store.list_approvals(state=state)

    @app.post("/api/approvals/{approval_id}/decision", dependencies=[api])
    async def decide(approval_id: str, body: DecisionBody) -> dict[str, Any]:
        try:
            return await runtime.approvals.decide(approval_id, body.decision, body.args, body.note)
        except KeyError as exc:
            raise HTTPException(404, "approval not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    # ---- tasks ------------------------------------------------------------------------------------------------
    @app.get("/api/tasks", dependencies=[api])
    async def tasks(state: str | None = None) -> list[dict[str, Any]]:
        return runtime.store.list_tasks(state=state)

    @app.post("/api/tasks", dependencies=[api])
    async def create_task(body: TaskBody) -> dict[str, Any]:
        try:
            return await runtime.tasks.start(body.instructions, body.title, body.harness, origin="user")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/tasks/{task_id}", dependencies=[api])
    async def get_task(task_id: str) -> dict[str, Any]:
        task = r404(runtime.store.get_task(task_id), "task")
        return {**task, "logs": runtime.store.task_logs(task_id)}

    @app.post("/api/tasks/{task_id}/steer", dependencies=[api])
    async def steer(task_id: str, body: SteerBody) -> dict[str, Any]:
        try:
            return runtime.tasks.steer(task_id, body.text)
        except KeyError as exc:
            raise HTTPException(404, "task not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/tasks/{task_id}/stop", dependencies=[api])
    async def stop(task_id: str) -> dict[str, Any]:
        try:
            return await runtime.tasks.stop(task_id)
        except KeyError as exc:
            raise HTTPException(404, "task not found") from exc

    # ---- views ------------------------------------------------------------------------------------------------
    @app.get("/api/activity", dependencies=[api])
    async def activity(
        limit: int = 100, before: int | None = None, level: str | None = None
    ) -> list[dict[str, Any]]:
        return runtime.store.list_activity(min(limit, 500), before, level)

    @app.get("/api/today", dependencies=[api])
    async def today() -> dict[str, Any]:
        return await runtime.today()

    @app.post("/api/brief/run", dependencies=[api])
    async def run_brief() -> dict[str, Any]:
        return await runtime.brief.run()

    @app.post("/api/checkin/run", dependencies=[api])
    async def run_checkin() -> dict[str, Any]:
        return await runtime.checkin()

    @app.get("/api/events", dependencies=[api])
    async def events(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return runtime.store.list_events(status=status, limit=min(limit, 500))

    # ---- artifacts --------------------------------------------------------------------------------------------
    @app.get("/api/artifacts", dependencies=[api])
    async def artifacts() -> list[dict[str, Any]]:
        return runtime.store.list_artifacts()

    @app.get("/api/artifacts/{artifact_id}", dependencies=[api])
    async def artifact(artifact_id: str) -> dict[str, Any]:
        return r404(runtime.store.get_artifact(artifact_id), "artifact")

    @app.get("/api/artifacts/{artifact_id}/download", dependencies=[api])
    async def download(artifact_id: str) -> FileResponse:
        art = r404(runtime.store.get_artifact(artifact_id), "artifact")
        return FileResponse(runtime.artifacts.path(art), filename=art["filename"])

    @app.get("/api/artifacts/{artifact_id}/preview", dependencies=[api])
    async def preview(artifact_id: str) -> HTMLResponse:
        art = r404(runtime.store.get_artifact(artifact_id), "artifact")
        # Shown in a sandboxed iframe; the CSP stops scripts and outbound requests either way.
        return HTMLResponse(
            runtime.artifacts.preview(art),
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # ---- memory, skills, audit, outbox --------------------------------------------------------------------------
    @app.get("/api/memory", dependencies=[api])
    async def memory() -> list[dict[str, Any]]:
        return runtime.memory.all()

    @app.post("/api/memory", dependencies=[api])
    async def add_memory(body: MemoryBody) -> dict[str, Any]:
        return runtime.memory.write(body.content, body.kind, body.key, source="user")

    @app.delete("/api/memory/{memory_id}", dependencies=[api])
    async def del_memory(memory_id: str) -> dict[str, Any]:
        if not runtime.memory.forget(memory_id):
            raise HTTPException(404, "memory not found")
        return {"ok": True}

    @app.get("/api/skills", dependencies=[api])
    async def skills() -> list[dict[str, str]]:
        return runtime.skills.public()

    @app.get("/api/audit", dependencies=[api])
    async def audit(limit: int = 100) -> list[dict[str, Any]]:
        return runtime.store.list_audit(min(limit, 1000))

    @app.get("/api/outbox", dependencies=[api])
    async def outbox() -> list[dict[str, Any]]:
        return runtime.outbox()

    app.include_router(evals_router(runtime, api))
    app.include_router(connections_router(runtime, auth))

    # ---- webhooks (own HMAC auth, no bearer) -------------------------------------------------------------------
    @app.post("/api/webhooks/{connector}")
    async def webhook(connector: str, request: Request) -> Response:
        conn = runtime.connectors.get(connector)
        if conn is None:
            raise HTTPException(404, "no such connector")
        raw = await request.body()
        payload: Any = {}
        if raw.strip():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "body must be a JSON object")
        req = PushRequest(
            payload, {k.lower(): v for k, v in request.headers.items()}, raw, dict(request.query_params)
        )
        try:
            res = await conn.handle_push(req)
        except WebhookAuthError as exc:
            raise HTTPException(401, str(exc)) from exc
        except NotImplementedError as exc:
            raise HTTPException(400, str(exc)) from exc
        result = await runtime.ingest(connector, res.events) if res.events else {}
        if res.body is not None:
            if isinstance(res.body, str):
                return Response(res.body, status_code=res.status, media_type=res.content_type)
            return JSONResponse(res.body, status_code=res.status)
        return JSONResponse({"accepted": len(res.events), **result}, status_code=res.status)

    # ---- voice --------------------------------------------------------------------------------------------------
    @app.post("/api/voice/transcribe", dependencies=[api])
    async def transcribe(file: UploadFile = File(...)) -> dict[str, Any]:
        v = runtime.cfg.voice
        if v.provider == "none":
            raise HTTPException(501, "server-side transcription is not configured (voice.provider)")
        audio = await file.read()
        if len(audio) > 25 * 1024 * 1024:
            raise HTTPException(413, "audio too large (25 MB max)")
        if v.provider == "mock":
            return {"text": "Build me a one-page cash summary for this week and email it to Sam."}
        key = runtime.secrets.get(v.api_key_env) or "not-needed"
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{(v.base_url or 'https://api.openai.com/v1').rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                data={"model": v.model},
                files={"file": (file.filename or "audio.webm", audio, file.content_type or "audio/webm")},
            )
        if r.status_code >= 400:
            raise HTTPException(502, f"transcription failed: {runtime.redactor.text(r.text[:300])}")
        return {"text": r.json().get("text", "")}

    app.include_router(push_router(runtime, auth))

    # ---- UI -------------------------------------------------------------------------------------------------------
    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception) -> Response:
        if request.url.path.startswith("/api/"):
            detail = getattr(exc, "detail", "not found")
            return JSONResponse({"detail": detail}, status_code=404)
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse(NO_UI, status_code=200)

    if (STATIC_DIR / "index.html").is_file():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    else:

        @app.get("/", response_class=HTMLResponse)
        async def no_ui() -> str:
            return NO_UI

    return app


NO_UI = """<!doctype html><meta charset=utf-8><title>Tandem</title><div id=app style="font:16px system-ui;max-width:640px;margin:4rem auto">
<h1>Tandem is running</h1><p>The web UI is not built yet. Run <code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>,
then reload. The API is at <a href="/api/docs">/api/docs</a>.</p></div>"""
