from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values, load_dotenv
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .store import Store
from .service import Platform, PlatformError
from . import runtime
from .evaluator import validate_case
from .demo import build_demo

class InputFile(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    content: str = Field(max_length=2_000_000)

class AgentInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    instructions: str = Field(min_length=1, max_length=20000)
    model: str | None = Field(default=None, max_length=160)
    # Number of eval cases to generate right after creation (0 = none).
    generate_evals: int = Field(default=0, ge=0, le=80)
    max_turns: int | None = Field(default=None, ge=1, le=40)
    auto_improve: bool = False
    auto_promote: bool = False
    cost_weight: float = Field(default=1.0, ge=0, le=1000)
    latency_weight: float = Field(default=0.0, ge=0, le=10)

class VersionInput(BaseModel):
    instructions: str | None = Field(default=None, min_length=1, max_length=20000)
    playbook: str | None = Field(default=None, max_length=8000)
    model: str | None = Field(default=None, max_length=160)
    max_turns: int | None = Field(default=None, ge=1, le=40)

class AgentPatch(BaseModel):
    auto_improve: bool | None = None
    auto_promote: bool | None = None
    # Reward = pass_rate - cost_weight * $/task - latency_weight * seconds/task.
    cost_weight: float | None = Field(default=None, ge=0, le=1000)
    latency_weight: float | None = Field(default=None, ge=0, le=10)
    # OpenRouter model IDs the climb may switch between.
    climb_models: list[str] | None = Field(default=None, max_length=12)

class GenerateInput(BaseModel):
    count: int = Field(default=8, ge=1, le=80)

class RunInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    files: list[InputFile] = Field(default_factory=list, max_length=10)
    token: str | None = Field(default=None, max_length=160)

class SessionInput(BaseModel):
    token: str | None = Field(default=None, max_length=160)

class FeedbackInput(BaseModel):
    rating: Literal["positive", "negative"]
    correction: str = Field(default="", max_length=8000)
    expected_name: str | None = Field(default=None, max_length=180)
    expected_content: str | None = Field(default=None, max_length=2_000_000)
    test_code: str | None = Field(default=None, max_length=60_000)

class CaseInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=20000)
    files: list[InputFile] = Field(default_factory=list, max_length=10)
    expected_name: str | None = Field(default=None, min_length=1, max_length=180)
    expected_content: str | None = Field(default=None, max_length=2_000_000)
    # Hidden test module (test_* functions) run against the agent's outputs/ files.
    test_code: str | None = Field(default=None, max_length=60_000)
    # Yes/no criteria graded by the LLM judge; may be combined with an expected file.
    rubric: list[str] = Field(default_factory=list, max_length=10)
    split: Literal["train", "validation", "holdout"] = "train"

class DeployInput(BaseModel):
    slug: str | None = Field(default=None, max_length=63)
    version_id: str | None = Field(default=None, max_length=80)
    rotate_key: bool = False

class AuditInput(BaseModel):
    batch_id: str = Field(min_length=1, max_length=80)


def create_app(data_dir=None):
    db = Store(data_dir)
    platform = Platform(db)

    @asynccontextmanager
    async def lifespan(app):
        # A separately launched worker may still own jobs in this local database.
        if os.getenv("RECOVER_JOBS_ON_START", "1") == "1":
            platform.recover()
        yield
        await platform.close()

    app = FastAPI(title="Brainbase Agent Platform", version="0.1.0", lifespan=lifespan)
    app.state.platform = platform

    @app.middleware("http")
    async def local_config(request: Request, call_next):
        # Allows a key saved during local development to become available without rebuilding.
        # Ignore blank template entries so an environment-supplied key survives.
        # Settings are loaded at startup. Never overwrite explicit process settings.
        local = dotenv_values(ROOT / ".env")
        for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY"):
            if local.get(key) and not os.getenv(key):
                os.environ[key] = local[key]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(PlatformError)
    async def platform_error(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo uploaded contents, credentials, or invalid Unicode in errors.
        errors = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"].encode("utf-8", "replace").decode()}
                  for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    def check_shared_run(run_id: str, token: str | None):
        run = platform.need("runs", run_id)
        if token is not None:
            if not run.get("deployment_id"):
                raise PlatformError("This run does not belong to a deployment.", 403)
            platform.authorize_deployment(run["deployment_id"], token)
        return run

    @app.get("/api/status")
    def status():
        return runtime.get_runtime_status()

    @app.get("/api/models")
    async def models():
        """Baseline model choices: every tool-capable OpenRouter model, cheapest first."""
        base = runtime.get_models()
        if base["provider"] != "openrouter":
            return base
        catalog = await runtime.climbable_models()
        if not catalog:
            return base
        return {**base, "models": [{"id": m["id"], "name": m["id"], "provider": "openrouter",
                                    "input_per_mtok": m["input_per_mtok"], "output_per_mtok": m["output_per_mtok"],
                                    "context_length": m["context_length"]} for m in catalog]}

    @app.get("/api/agents")
    def agents():
        return [platform.agent_view(a["id"]) for a in db.all("agents")]

    @app.post("/api/agents", status_code=201)
    async def create_agent(data: AgentInput):
        values = data.model_dump()
        if values.get("model") and runtime.get_models()["provider"] == "openrouter":
            catalog = await runtime.climbable_models()
            if catalog and values["model"] not in {m["id"] for m in catalog}:
                raise PlatformError("Choose a tool-capable OpenRouter model from the list.", 422)
        agent = platform.create_agent(values)
        if values["generate_evals"]:
            try:
                agent["generation"] = await platform.generate_evals(agent["id"], values["generate_evals"])
            except PlatformError as exc:
                agent["generation"] = {"status": "error", "error": exc.detail}
        return agent

    @app.post("/api/agents/{agent_id}/cases/generate", status_code=202)
    async def generate_cases(agent_id: str, data: GenerateInput):
        return await platform.generate_evals(agent_id, data.count)

    @app.get("/api/agents/{agent_id}/generations")
    def generations(agent_id: str):
        platform.need("agents", agent_id)
        return db.all("generations", agent_id=agent_id)

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str):
        return platform.agent_view(agent_id) | {"versions": db.all("versions", agent_id=agent_id)}

    @app.patch("/api/agents/{agent_id}")
    async def patch_agent(agent_id: str, data: AgentPatch):
        platform.need("agents", agent_id)
        changes = data.model_dump(exclude_none=True)
        if changes:
            db.update("agents", agent_id, **changes)
        if data.auto_improve:
            platform.spawn(platform.maybe_auto(agent_id))
        return platform.agent_view(agent_id)

    @app.post("/api/agents/{agent_id}/versions", status_code=201)
    def create_version(agent_id: str, data: VersionInput):
        return platform.create_version(agent_id, data.model_dump(exclude_none=True))

    @app.post("/api/agents/{agent_id}/runs", status_code=202)
    async def run_agent(agent_id: str, data: RunInput):
        return platform.submit_run(agent_id, data.prompt, [f.model_dump() for f in data.files])

    @app.get("/api/agents/{agent_id}/runs")
    def agent_runs(agent_id: str):
        platform.need("agents", agent_id)
        lookups = platform.trace_lookups(agent_id)
        return [platform.run_view(r["id"], lookups=lookups) for r in db.all("runs", agent_id=agent_id)]

    def owner_session(agent_id, session_id):
        session = platform.need("sessions", session_id)
        if session["agent_id"] != agent_id:
            raise PlatformError("Conversation does not belong to this agent.", 404)
        return session

    @app.get("/api/agents/{agent_id}/sessions")
    def sessions(agent_id: str):
        platform.need("agents", agent_id)
        values = sorted(db.all("sessions", agent_id=agent_id), key=lambda s: s["updated_at"], reverse=True)
        return [platform.session_view(s["id"], details=False) for s in values[:50]]

    @app.post("/api/agents/{agent_id}/sessions", status_code=201)
    async def create_session(agent_id: str):
        return platform.create_session(agent_id)

    @app.get("/api/agents/{agent_id}/sessions/{session_id}")
    def get_session(agent_id: str, session_id: str):
        owner_session(agent_id, session_id)
        return platform.session_view(session_id)

    @app.post("/api/agents/{agent_id}/sessions/{session_id}/runs", status_code=202)
    async def session_run(agent_id: str, session_id: str, data: RunInput):
        owner_session(agent_id, session_id)
        return platform.submit_session_run(session_id, data.prompt, [f.model_dump() for f in data.files])

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, token: str | None = None):
        check_shared_run(run_id, token)
        return platform.run_view(run_id, details=True)

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str, after_seq: int = 0, token: str | None = None):
        check_shared_run(run_id, token)
        return db.events(run_id, after_seq)

    def artifact_file(artifact_id, token):
        a = platform.need("artifacts", artifact_id)
        check_shared_run(a["run_id"], token)
        path = Path(a["path"])
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(db.data_dir / "artifacts"):
            raise PlatformError("Artifact is unavailable.", 404)
        return a, path

    @app.get("/api/artifacts/{artifact_id}/preview")
    def artifact_preview(artifact_id: str, token: str | None = None):
        a, path = artifact_file(artifact_id, token)
        limit = 128 * 1024
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        if b"\x00" in raw:
            raise PlatformError("This file cannot be previewed as text. Download it instead.", 415)
        truncated = len(raw) > limit
        # A byte limit may split a UTF-8 character; trim only that incomplete suffix.
        import codecs
        try:
            decoder = codecs.getincrementaldecoder("utf-8")()
            content = decoder.decode(raw[:limit], final=not truncated)
        except UnicodeDecodeError:
            raise PlatformError("This file is not UTF-8 text. Download it instead.", 415)
        return {"name": a["name"], "content": content, "size": path.stat().st_size, "truncated": truncated}

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str, token: str | None = None):
        a, path = artifact_file(artifact_id, token)
        suffix = Path(a["name"]).suffix.lower()
        media = {".csv": "text/csv", ".json": "application/json", ".txt": "text/plain", ".py": "text/plain"}.get(suffix, "application/octet-stream")
        return FileResponse(path, media_type=media, filename=Path(a["name"]).name)

    @app.post("/api/runs/{run_id}/feedback", status_code=201)
    async def feedback(run_id: str, data: FeedbackInput):
        if (data.expected_name is None) != (data.expected_content is None):
            raise PlatformError("Provide both expected filename and contents.", 422)
        if data.expected_name:
            try:
                validate_case({"expected_name": data.expected_name, "expected_content": data.expected_content})
            except ValueError as exc:
                raise PlatformError(str(exc), 422)
        return platform.feedback(run_id, data.model_dump())

    @app.get("/api/agents/{agent_id}/cases")
    def cases(agent_id: str):
        platform.need("agents", agent_id)
        return db.all("cases", agent_id=agent_id)

    @app.post("/api/agents/{agent_id}/cases", status_code=201)
    def create_case(agent_id: str, data: CaseInput):
        value = data.model_dump()
        if (value["expected_name"] is None) != (value["expected_content"] is None):
            raise PlatformError("Provide both expected filename and contents, or neither.", 422)
        return platform.add_case(agent_id, value)

    @app.get("/api/agents/{agent_id}/improvements")
    def improvements(agent_id: str):
        platform.need("agents", agent_id)
        return [batch_view(b) for b in db.all("batches", agent_id=agent_id)]

    def batch_view(batch):
        runs = db.all("runs", batch_id=batch["id"])
        return {**batch, "progress": {"completed": sum(r["status"] in {"completed", "failed", "interrupted"} for r in runs),
                "running": sum(r["status"] == "running" for r in runs), "queued": sum(r["status"] == "queued" for r in runs),
                "scheduled": len(runs), "planned_max": batch.get("planned_max_runs")}}

    @app.post("/api/agents/{agent_id}/improvements", status_code=202)
    async def improve(agent_id: str):
        return await platform.start_batch(agent_id)

    @app.get("/api/improvements/{batch_id}")
    def improvement(batch_id: str):
        return batch_view(platform.need("batches", batch_id))

    @app.post("/api/improvements/{batch_id}/promote")
    async def promote(batch_id: str):
        return platform.decide_batch(batch_id, "promote")

    @app.post("/api/improvements/{batch_id}/reject")
    async def reject(batch_id: str):
        return platform.decide_batch(batch_id, "reject")

    @app.get("/api/agents/{agent_id}/audits")
    def audits(agent_id: str):
        platform.need("agents", agent_id)
        return db.all("audits", agent_id=agent_id)

    @app.post("/api/agents/{agent_id}/audits", status_code=202)
    async def final_audit(agent_id: str, data: AuditInput):
        from .audit import start_audit
        return await start_audit(platform, agent_id, data.batch_id)

    @app.get("/api/audits/{audit_id}")
    def audit(audit_id: str):
        return platform.need("audits", audit_id)

    @app.post("/api/agents/{agent_id}/deploy")
    def deploy(agent_id: str, data: DeployInput):
        return platform.deploy(agent_id, data.slug, data.version_id, data.rotate_key)

    @app.get("/api/deployments/{slug}")
    def shared_deployment(slug: str, token: str | None = None, request: Request = None):
        key = token or (request.headers.get("X-Deployment-Key") if request else None)
        d = platform.authorize_deployment(slug, key)
        a = platform.need("agents", d["agent_id"])
        return {"agent_name": a["name"], "description": a["description"], "version_id": d["version_id"], "slug": slug}

    @app.post("/api/deployments/{slug}/runs", status_code=202)
    async def run_deployment(slug: str, data: RunInput, request: Request):
        key = request.headers.get("X-Deployment-Key") or data.token
        d = platform.authorize_deployment(slug, key)
        return platform.submit_run(d["agent_id"], data.prompt, [f.model_dump() for f in data.files], "deployment", d)

    def deployment_session(slug, session_id, key):
        deployment = platform.authorize_deployment(slug, key)
        session = platform.need("sessions", session_id)
        if session["deployment_id"] != deployment["id"]:
            raise PlatformError("Conversation does not belong to this deployment.", 404)
        return session

    @app.post("/api/deployments/{slug}/sessions", status_code=201)
    async def create_shared_session(slug: str, data: SessionInput, request: Request):
        deployment = platform.authorize_deployment(slug, request.headers.get("X-Deployment-Key") or data.token)
        return platform.create_session(deployment["agent_id"], deployment)

    @app.get("/api/deployments/{slug}/sessions/{session_id}")
    def get_shared_session(slug: str, session_id: str, request: Request, token: str | None = None):
        deployment_session(slug, session_id, request.headers.get("X-Deployment-Key") or token)
        return platform.session_view(session_id)

    @app.post("/api/deployments/{slug}/sessions/{session_id}/runs", status_code=202)
    async def shared_session_run(slug: str, session_id: str, data: RunInput, request: Request):
        deployment_session(slug, session_id, request.headers.get("X-Deployment-Key") or data.token)
        return platform.submit_session_run(session_id, data.prompt, [f.model_dump() for f in data.files])

    @app.post("/api/demo/seed", status_code=201)
    def seed():
        # Seed definitions only. All visible runs/metrics come from real executions.
        prior = [a for a in db.all("agents") if a.get("demo")]
        if prior:
            return platform.agent_view(prior[0]["id"])
        demo = build_demo()
        a = platform.create_agent(demo)
        db.update("agents", a["id"], demo=True, demo_prompt=demo["prompt"], demo_files=demo["files"],
                  demo_correction=demo["correction"], demo_expected=demo["cases"][0]["expected_content"])
        for case in demo["cases"]:
            platform.add_case(a["id"], case)
        return platform.agent_view(a["id"])

    @app.get("/health")
    def health():
        return {"status": "ok"}

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found.")
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        return JSONResponse({"message": "API is running. Start the frontend with npm run dev in frontend/, or build it with npm run build.", "docs": "/docs"})

    return app

app = create_app()
