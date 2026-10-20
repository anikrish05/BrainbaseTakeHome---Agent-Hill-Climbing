from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from functools import wraps
from pathlib import Path

from .store import Store, new_id, now
from . import runtime
from .evaluator import evaluate_output, validate_case

ACTIVE_BATCH = {"queued", "analyzing", "proposing", "evaluating", "confirming", "awaiting_review"}

# Reward = pass_rate - cost_weight * mean $ per task - latency_weight * mean seconds per task.
# A cost_weight of 1.0 means one cent per task costs one point (0.01) of pass rate.
DEFAULT_COST_WEIGHT = 1.0
DEFAULT_LATENCY_WEIGHT = 0.0
# A candidate with equal quality qualifies if it is at least this much cheaper.
# Single-trial cost is noisy (failing runs burn more turns), so the margin is wide
# and a cost-only win also requires the candidate to pass at least one case.
COST_IMPROVEMENT_MARGIN = 0.25


# Per-case score changes smaller than this are noise, not a fix or a regression.
SCORE_EPSILON = 0.099
DEFAULT_CLIMB_MODELS = ["openai/gpt-4.1-mini", "anthropic/claude-haiku-5.5", "deepseek/deepseek-v4-flash",
                        "google/gemini-3.1-flash-lite", "openai/gpt-4.1"]
TURN_RANGE = (4, runtime.MAX_TURNS_LIMIT)


def climb_models(agent, catalog=None):
    """Models the learner may choose. An explicit list (agent or CLIMB_MODELS) narrows the
    search; otherwise the whole OpenRouter catalog of tool-capable models is available."""
    configured = [m.strip() for m in os.getenv("CLIMB_MODELS", "").split(",") if m.strip()]
    explicit = agent.get("climb_models") or configured
    if explicit:
        return list(dict.fromkeys(explicit))
    if catalog:
        return [m["id"] for m in catalog]
    return list(DEFAULT_CLIMB_MODELS)


def _timeout_for(max_turns):
    return int(min(600, max(120, 15 * max_turns)))


def _failure_signature(entry):
    error = entry.get("error") or ""
    if error.startswith("Agent did not finish"):
        return "agent: " + ("ran out of turns" if "Max turns" in error else "timed out" if "time" in error.lower() else "no usable output")
    match = re.search(r"([A-Z][A-Za-z]*(?:Error|Exception))", error)
    if match:
        return match.group(1)
    for marker, label in (("was not produced", "missing output file"), ("differs", "wrong output values"),
                          ("row_count", "wrong row count"), ("columns", "wrong columns"), ("Judge:", "rubric criteria unmet")):
        if marker in error:
            return label
    return "other"


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None

class PlatformError(Exception):
    def __init__(self, detail: str, status: int = 400):
        self.detail, self.status = detail, status


def clean_name(name: str) -> str:
    if not name or name.startswith("/") or "\\" in name or ":" in name or any(ord(c) < 32 for c in name) or any(p in {"..", ".", ""} for p in name.split("/")) or len(name) > 180:
        raise PlatformError("Use a relative filename without traversal or empty path segments.", 422)
    return name


def clean_files(files: list[dict]) -> list[dict]:
    if len(files) > 10:
        raise PlatformError("A run accepts at most 10 files.", 422)
    total, names, result = 0, set(), []
    for f in files:
        name = clean_name(f["name"])
        if name.split("/")[0] == "outputs":
            raise PlatformError("The outputs directory is reserved for generated files.", 422)
        if name in names:
            raise PlatformError("Input filenames must be unique.", 422)
        content = f["content"]
        total += len(content.encode("utf-8"))
        if total > 2_000_000:
            raise PlatformError("Combined input files must be smaller than 2 MB.", 422)
        names.add(name)
        result.append({"name": name, "content": content})
    return result


def _cut(text, limit):
    """Shorten at a line boundary and say how much was cut, so a model never mistakes a cut for the real end."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    if "\n" in head[limit // 2:]:
        head = head[:head.rfind("\n")]
    return head + f"\n[... cut: {len(text) - len(head)} more characters]"


def scrub(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if re.search(r"api.?key|authorization|secret|password", k, re.I) else scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value[:100]]
    if isinstance(value, str):
        value = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[redacted]", value)
        for key in (os.getenv("OPENAI_API_KEY"), os.getenv("OPENROUTER_API_KEY")):
            if key:
                value = value.replace(key, "[redacted]")
        return value[:24000]
    return value

def serialized(method):
    """Keep local pointer/version transitions indivisible across API threads."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.db.lock:
            return method(self, *args, **kwargs)
    return wrapped

class Platform:
    def __init__(self, store: Store):
        self.db = store
        self.semaphore = asyncio.Semaphore(int(os.getenv("RUN_CONCURRENCY", "2")))
        self.eval_semaphore = asyncio.Semaphore(int(os.getenv("EVAL_CONCURRENCY", "6")))
        self.tasks: set[asyncio.Task] = set()
        self.timers: dict[str, asyncio.Task] = {}
        self.batch_lock = asyncio.Lock()
        self.shutting_down = False

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def recover(self):
        for r in self.db.all("runs"):
            if r["status"] in {"queued", "running"}:
                self.db.update("runs", r["id"], status="interrupted", error="Server restarted before this run finished.", finished_at=now())
                self.db.event(r["id"], "run.error", {"error": "Run interrupted by server restart."})
        for b in self.db.all("batches"):
            if b["status"] in ACTIVE_BATCH - {"awaiting_review"}:
                self.db.update("batches", b["id"], status="interrupted", error="Server restarted. Evidence is available for another batch.", finished_at=now())
        for g in self.db.all("generations"):
            if g["status"] == "running":
                self.db.update("generations", g["id"], status="interrupted", stage="done", error="Server restarted during generation.", finished_at=now())
        for audit in self.db.all("audits"):
            if audit["status"] in {"queued", "running"}:
                self.db.update("audits", audit["id"], status="interrupted", error="Server restarted during the final audit. Do not reuse exposed final cases for candidate selection.", finished_at=now())

    async def close(self):
        self.shutting_down = True
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    def need(self, kind, identity):
        value = self.db.get(kind, identity)
        if value is None:
            raise PlatformError(f"{kind.rstrip('s').capitalize()} not found.", 404)
        return value

    def deployment(self, agent_id):
        entries = self.db.all("deployments", agent_id=agent_id)
        return entries[0] if entries else None

    def current_version(self, agent_id):
        a = self.need("agents", agent_id)
        d = self.deployment(agent_id)
        return self.need("versions", d["version_id"] if d else a["current_version_id"])

    def agent_view(self, identity):
        agent = dict(self.need("agents", identity))
        agent["current_version"] = self.need("versions", agent["current_version_id"])
        d = self.deployment(identity)
        agent["deployment"] = self.public_deployment(d) if d else None
        runs = [r for r in self.db.all("runs", agent_id=identity) if r["source"] != "eval"]
        labeled = [r for r in runs if r.get("outcome") in {"passed", "failed"}]
        costs = [r["cost_usd"] for r in runs if r.get("cost_usd") is not None]
        agent["stats"] = {"runs": len(runs), "failed": sum(r["status"] in {"failed", "interrupted"} for r in runs),
                          "labeled": len(labeled), "passed": sum(r["outcome"] == "passed" for r in labeled),
                          "cost_usd": sum(costs) if costs else None, "versions": len(self.db.all("versions", agent_id=identity))}
        agent["eligible_evidence"] = len(self.pending_evidence(identity))
        agent["needs_label"] = sum(f["status"] in {"needs_label", "needs_context"} for f in self.db.all("feedback", agent_id=identity))
        return agent

    def create_agent(self, data):
        identity = new_id("agt")
        a = {"id": identity, "name": data["name"], "description": data.get("description", ""),
             "created_at": now(), "auto_improve": bool(data.get("auto_improve", False)),
             "auto_promote": bool(data.get("auto_promote", False)),
             "cost_weight": float(data.get("cost_weight", DEFAULT_COST_WEIGHT)),
             "latency_weight": float(data.get("latency_weight", DEFAULT_LATENCY_WEIGHT)),
             "current_version_id": ""}
        self.db.put("agents", a)
        self.create_version(identity, data, created_by="user")
        return self.agent_view(identity)

    @serialized
    def create_version(self, agent_id, data, created_by="user", parent_id=None, batch_id=None, make_current=True):
        agent = self.need("agents", agent_id)
        status = runtime.get_runtime_status()
        parent_id = parent_id or agent.get("current_version_id") or None
        parent = self.db.get("versions", parent_id) if parent_id else {}
        config = {"instructions": data.get("instructions", parent.get("instructions", "")),
                  "playbook": data.get("playbook", parent.get("playbook", "")),
                  "model": data.get("model") or parent.get("model") or status["model"],
                  "provider": data.get("provider") or parent.get("provider") or status["provider"],
                  "transport": data.get("transport") or parent.get("transport") or os.getenv("MODEL_TRANSPORT", "responses"),
                  "max_turns": max(1, min(runtime.MAX_TURNS_LIMIT, int(data.get("max_turns") or parent.get("max_turns") or 12))),
                  "max_tokens": int(data.get("max_tokens") or parent.get("max_tokens") or 4000)}
        config["timeout_s"] = _timeout_for(config["max_turns"])
        if len(config["instructions"]) > 20000 or len(config["playbook"]) > 8000:
            raise PlatformError("Instructions or playbook exceed the supported size.", 422)
        version = {"id": new_id("ver"), "agent_id": agent_id, **config,
                   "number": len(self.db.all("versions", agent_id=agent_id)) + 1,
                   "parent_id": parent_id, "created_by": created_by, "batch_id": batch_id,
                   "created_at": now(), "runtime_version": runtime.RUNTIME_VERSION,
                   "sandbox_image": status.get("sandbox", {}).get("image_id") or os.getenv("SANDBOX_IMAGE", "python:3.12-slim")}
        self.db.put("versions", version)
        if make_current:
            self.db.update("agents", agent_id, current_version_id=version["id"])
        return version

    def public_deployment(self, d):
        return {k: d[k] for k in ("slug", "agent_id", "version_id", "created_at")} | {"url": f"/d/{d['slug']}"}

    @serialized
    def deploy(self, agent_id, slug=None, version_id=None, rotate_key=False):
        agent = self.need("agents", agent_id)
        version = self.need("versions", version_id or agent["current_version_id"])
        if version["agent_id"] != agent_id:
            raise PlatformError("Version does not belong to this agent.", 422)
        existing = self.deployment(agent_id)
        token = secrets.token_urlsafe(32) if not existing or rotate_key else None
        if existing:
            if slug and slug != existing["slug"]:
                raise PlatformError("A deployment slug stays stable. Use the existing slug.", 409)
            d = {**existing, "version_id": version["id"]}
            if token:
                d["token_hash"] = hashlib.sha256(token.encode()).hexdigest()
        else:
            slug = slug or re.sub(r"[^a-z0-9]+", "-", agent["name"].lower()).strip("-")[:36] or agent_id
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug):
                raise PlatformError("Slug must contain lowercase letters, numbers, and hyphens.", 422)
            if self.db.get("deployments", slug):
                raise PlatformError("That deployment slug is already in use.", 409)
            d = {"id": slug, "slug": slug, "agent_id": agent_id, "version_id": version["id"],
                 "token_hash": hashlib.sha256(token.encode()).hexdigest(), "created_at": now()}
        self.db.put("deployments", d)
        if version_id:
            self.db.update("agents", agent_id, current_version_id=version["id"])
        self.db.put("deployment_changes", {"id": new_id("change"), "deployment_id": d["id"],
                    "old_version_id": existing["version_id"] if existing else None, "version_id": version["id"], "created_at": now()})
        return self.public_deployment(d) | {"token": token, "url": f"/d/{d['slug']}" + (f"?token={token}" if token else "")}

    def authorize_deployment(self, slug, token):
        d = self.need("deployments", slug)
        if not token or not secrets.compare_digest(d["token_hash"], hashlib.sha256(token.encode()).hexdigest()):
            raise PlatformError("A valid deployment key is required.", 403)
        return d

    def trace_lookups(self, agent_id):
        tables = {kind: {v['id']: v for v in self.db.all(kind, agent_id=agent_id)}
                  for kind in ('versions', 'cases', 'batches', 'audits')}
        tables['batch_numbers'] = {b['id']: i + 1 for i, b in enumerate(sorted(
            tables['batches'].values(), key=lambda b: (b.get('created_at', ''), b['id'])))}
        attempts, counts = {}, {}
        for r in sorted(self.db.all('runs', agent_id=agent_id), key=lambda r: (r.get('created_at', ''), r['id'])):
            if r.get('case_id'):
                key = (r.get('batch_id'), r.get('audit_id'), r['version_id'], r['case_id'])
                counts[key] = counts.get(key, 0) + 1
                attempts[r['id']] = counts[key]
        tables['attempts'] = attempts
        return tables

    def trace_context(self, run, lookups):
        version = lookups['versions'].get(run['version_id'], {})
        batch = lookups['batches'].get(run.get('batch_id'), {})
        audit = lookups['audits'].get(run.get('audit_id'), {})
        case = lookups['cases'].get(run.get('case_id'), {})
        snapshot = run.get('evaluation_context', {})
        split = snapshot.get('case_split') or case.get('split')
        attempt = snapshot.get('attempt') or lookups['attempts'].get(run['id'])
        candidate_number = None
        if batch:
            candidates = sorted((v for v in lookups['versions'].values() if v.get('batch_id') == batch['id']), key=lambda v: v['number'])
            candidate_number = next((i + 1 for i, v in enumerate(candidates) if v['id'] == run['version_id']), None)
        if audit:
            kind = 'holdout'
            role = 'baseline' if run['version_id'] == audit['parent_version_id'] else 'candidate'
            label = f"Holdout · {role}"
        elif batch:
            role = 'baseline' if run['version_id'] == batch['parent_version_id'] else 'candidate'
            kind = role
            label = 'Baseline' if role == 'baseline' else f"Candidate {candidate_number}" if candidate_number else 'Candidate'
        elif run.get('session_id'):
            kind, role = ('team_chat' if run['source'] == 'deployment' else 'chat'), None
            label = 'Deployed chat' if kind == 'team_chat' else 'Builder chat'
        else:
            kind, role = ('evaluation' if run['source'] == 'eval' else 'one_shot'), None
            label = 'Evaluation' if kind == 'evaluation' else 'Deployed one-shot' if run['source'] == 'deployment' else 'Builder one-shot'
        phase = snapshot.get('phase')
        if not phase and run['source'] == 'eval':
            phase = 'holdout' if audit else 'confirmation' if batch and split == 'validation' and (attempt or 0) > 1 else 'initial'
        return {'kind': kind, 'label': label, 'role': role, 'candidate_number': candidate_number,
                'version_number': version.get('number'), 'model': version.get('model'),
                'batch_id': batch.get('id') or audit.get('batch_id'),
                'batch_number': lookups['batch_numbers'].get(batch.get('id') or audit.get('batch_id')),
                'audit_id': audit.get('id'), 'case_name': snapshot.get('case_name') or case.get('name'),
                'case_split': split, 'phase': phase, 'attempt': attempt,
                'session_id': run.get('session_id'), 'turn_index': run.get('turn_index'),
                'metadata_source': 'recorded' if snapshot else 'derived'}

    def run_view(self, identity, details=False, *, lookups=None):
        run = dict(self.need("runs", identity))
        run.pop("config", None)
        run.pop("files", None)
        run.pop("conversation_context", None)
        run['trace_context'] = self.trace_context(run, lookups or self.trace_lookups(run['agent_id']))
        run['version_number'] = run['trace_context']['version_number']
        feedback = self.db.all("feedback", run_id=identity)
        run["feedback"] = feedback[0] if feedback else None
        if details:
            run["events"] = self.db.events(identity)
            run["artifacts"] = [{k: v for k, v in a.items() if k != "path"} | {"url": f"/api/artifacts/{a['id']}"}
                                for a in self.db.all("artifacts", run_id=identity)]
        return run

    def prepare_run(self, version, prompt, files, source="playground", deployment_id=None, batch_id=None, case_id=None):
        files = clean_files(files)
        if not prompt.strip() or len(prompt) > 20000:
            raise PlatformError("A prompt of 1–20,000 characters is required.", 422)
        run = {"id": new_id("run"), "agent_id": version["agent_id"], "version_id": version["id"],
               "config": version, "source": source, "deployment_id": deployment_id, "batch_id": batch_id, "case_id": case_id,
               "trial_id": new_id("trial"), "prompt": prompt, "files": files, "status": "queued", "outcome": "ungraded",
               "output": "", "error": None, "cost_usd": None, "cost_kind": "unknown", "tokens_in": 0, "tokens_out": 0,
               "tool_calls": 0, "latency_ms": None, "created_at": now(), "finished_at": None}
        self.db.put("runs", run)
        return run

    def submit_run(self, agent_id, prompt, files, source="playground", deployment=None):
        self.check_ready()
        agent = self.need("agents", agent_id)
        version = self.need("versions", deployment["version_id"] if deployment else agent["current_version_id"])
        run = self.prepare_run(version, prompt, files, source, deployment["id"] if deployment else None)
        self.spawn(self.execute(run))
        return self.run_view(run["id"])

    @serialized
    def create_session(self, agent_id, deployment=None):
        agent = self.need("agents", agent_id)
        version = self.need("versions", deployment["version_id"] if deployment else agent["current_version_id"])
        session = {"id": new_id("chat"), "agent_id": agent_id, "version_id": version["id"],
                   "deployment_id": deployment["id"] if deployment else None,
                   "title": "New conversation", "created_at": now(), "updated_at": now()}
        self.db.put("sessions", session)
        return self.session_view(session["id"])

    def session_runs(self, session_id):
        return sorted(self.db.all("runs", session_id=session_id), key=lambda r: r["turn_index"])

    def session_view(self, session_id, details=True):
        session = dict(self.need("sessions", session_id))
        version = self.need("versions", session["version_id"])
        runs = self.session_runs(session_id)
        session.update(version_number=version["number"], model=version["model"], turn_count=len(runs),
                       active_run_id=next((r["id"] for r in runs if r["status"] in {"queued", "running"}), None))
        if details:
            lookups = self.trace_lookups(session['agent_id'])
            session["runs"] = [self.run_view(r["id"], lookups=lookups) | {
                "input_names": [f["name"] for f in r["files"]],
                "artifacts": [{k: v for k, v in a.items() if k != "path"} | {"url": f"/api/artifacts/{a['id']}"}
                              for a in self.db.all("artifacts", run_id=r["id"]) if a["role"] == "output"]} for r in runs]
        return session

    def conversation_context(self, runs):
        # Keep complete user/assistant pairs, newest first while budgeting, then restore order.
        # No old tool calls are replayed; their files remain in the session workspace.
        pairs, used = [], 0
        for run in reversed(runs[-12:]):
            names = [f["name"] for f in run["files"]]
            output_names = [a["name"] for a in self.db.all("artifacts", run_id=run["id"]) if a["role"] == "output"]
            user = _cut(run["prompt"], 2000) + ("\nAttached files: " + ", ".join(names) if names else "")
            reply = _cut(run.get("output") or "", 3000)
            if run["status"] != "completed":
                reply = f"[Previous turn {run['status']}: {run.get('error') or 'did not complete'}. Files may contain partial work.]"
            reply += "\nWorkspace output files: " + (", ".join("outputs/" + n for n in output_names) or "none published")
            if used + len(user) + len(reply) > 24_000:
                break
            pairs.append([{"role": "user", "content": user}, {"role": "assistant", "content": reply}])
            used += len(user) + len(reply)
        return [message for pair in reversed(pairs) for message in pair]

    @serialized
    def submit_session_run(self, session_id, prompt, files):
        self.check_ready()
        session = self.need("sessions", session_id)
        runs = self.session_runs(session_id)
        if any(r["status"] in {"queued", "running"} for r in runs):
            raise PlatformError("Wait for this conversation's current turn to finish before sending another message.", 409)
        if len(runs) >= 50:
            raise PlatformError("This conversation has reached 50 turns. Start a new chat.", 409)
        version = self.need("versions", session["version_id"])
        run = self.prepare_run(version, prompt, files, "deployment" if session["deployment_id"] else "playground", session["deployment_id"])
        context = self.conversation_context(runs)
        run = self.db.update("runs", run["id"], session_id=session_id, turn_index=len(runs) + 1,
                             conversation_context=context, context_run_ids=[r["id"] for r in runs[-len(context)//2:]] if context else [])
        self.db.update("sessions", session_id, title=session["title"] if runs else prompt.strip()[:80], updated_at=now())
        self.spawn(self.execute(run))
        return self.run_view(run["id"])

    def check_ready(self):
        status = runtime.get_runtime_status()
        if not status["ready"]:
            raise PlatformError(status.get("reason") or "Configure a provider key and start Docker first.", 409)

    async def execute(self, run):
        try:
            return await self._execute(run)
        except asyncio.CancelledError:
            # Includes cancellation while waiting for a concurrency slot.
            record = self.db.get("runs", run["id"])
            if record and record["status"] in {"queued", "running"}:
                self.db.update("runs", run["id"], status="interrupted", error="Run interrupted while queued.", finished_at=now())
                self.db.event(run["id"], "run.error", {"error": "Run interrupted while queued."})
            raise

    async def _execute(self, run):
        identity = run["id"]
        work_dir = (self.db.data_dir / "sessions" / run["session_id"] / "workspace" if run.get("session_id")
                    else self.db.data_dir / "runs" / identity / "workspace")
        work_dir.mkdir(parents=True, exist_ok=True)
        async def emit(kind, payload):
            self.db.event(identity, kind, scrub(payload))
        async with (self.eval_semaphore if run["source"] == "eval" else self.semaphore):
            start = time.monotonic()
            self.db.update("runs", identity, status="running")
            await emit("run.start", {"version_id": run["version_id"], "model": run["config"]["model"], "source": run["source"]})
            try:
                for f in run["files"]:
                    aid = new_id("file")
                    dest = self.db.data_dir / "artifacts" / aid
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(f["content"])
                    self.db.put("artifacts", {"id": aid, "run_id": identity, "name": f["name"], "path": str(dest), "size": dest.stat().st_size, "role": "input"})
                extra = {"conversation": run["conversation_context"]} if run.get("session_id") else {}
                if extra:
                    await emit("conversation.context", {"session_id": run["session_id"], "turn_index": run["turn_index"],
                        "history_turns": len(run["conversation_context"]) // 2, "workspace": "persistent",
                        "omitted_turns": run["turn_index"] - 1 - len(run["conversation_context"]) // 2})
                result = await asyncio.wait_for(runtime.execute_run(run["config"], run["prompt"], run["files"], work_dir, emit, **extra),
                                                timeout=int(run["config"].get("timeout_s", 120)) + 60)
                artifacts = []
                for output in result.get("artifacts", []):
                    source = Path(output["path"])
                    if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(work_dir.resolve()):
                        raise ValueError("Sandbox returned an unsafe artifact path.")
                    name = clean_name(output["name"])
                    if source.stat().st_size > 2_000_000:
                        continue
                    artifact_id = new_id("file")
                    dest = self.db.data_dir / "artifacts" / artifact_id
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, dest)
                    a = {"id": artifact_id, "run_id": identity, "name": name, "path": str(dest), "size": dest.stat().st_size,
                         "role": "output", "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}
                    self.db.put("artifacts", a)
                    artifacts.append(a)
                    await emit("artifact.produced", {"name": name, "size": a["size"], "artifact_id": artifact_id})
                updates = {k: result[k] for k in ("output", "tokens_in", "tokens_out", "cost_usd", "cost_kind", "tool_calls") if k in result}
                self.db.update("runs", identity, **updates, status="completed", latency_ms=round((time.monotonic()-start)*1000), finished_at=now())
                await emit("run.end", {"status": "completed", "cost_usd": result.get("cost_usd"), "artifact_count": len(artifacts)})
                return self.db.get("runs", identity), artifacts
            except asyncio.CancelledError:
                self.db.update("runs", identity, status="interrupted", error="Run interrupted.", finished_at=now())
                await emit("run.error", {"error": "Run interrupted."})
                raise
            except Exception as exc:
                message = scrub(str(exc) or type(exc).__name__)
                usage = getattr(exc, "usage", {})
                category = getattr(exc, "category", None) or ("agent" if isinstance(exc, TimeoutError) else None)
                self.db.update("runs", identity, **{k: usage[k] for k in ("tokens_in", "tokens_out", "cost_usd", "cost_kind", "tool_calls") if k in usage}, status="failed", error=message, error_category=category, latency_ms=round((time.monotonic()-start)*1000), finished_at=now())
                await emit("run.error", {"error": message, "category": category or type(exc).__name__})
                return self.db.get("runs", identity), []

    def add_case(self, agent_id, data):
        self.need("agents", agent_id)
        rubric = [str(item).strip()[:500] for item in (data.get("rubric") or []) if str(item).strip()][:10]
        has_expected = bool(data.get("expected_name"))
        test_code = data.get("test_code") or ""
        if test_code:
            if len(test_code) > 60_000 or "def test_" not in test_code:
                raise PlatformError("Test code must define test_* functions and stay under 60 KB.", 422)
            try:
                compile(test_code, "test_hidden.py", "exec")
            except SyntaxError as exc:
                raise PlatformError(f"Test code does not compile: {exc}", 422) from exc
        if not has_expected and not rubric and not test_code:
            raise PlatformError("A case needs hidden tests, an expected output file, rubric criteria, or a combination.", 422)
        if has_expected:
            try:
                validate_case(data)
            except ValueError as exc:
                raise PlatformError(str(exc), 422) from exc
            clean_name(data["expected_name"])
        files = clean_files(data.get("files", []))
        case = {"id": new_id("case"), "agent_id": agent_id, "name": data["name"], "prompt": data["prompt"],
                "files": files, "expected_name": data.get("expected_name"), "expected_content": data.get("expected_content"),
                "rubric": rubric, "origin": data.get("origin", "user"), "tags": list(data.get("tags") or [])[:10],
                "test_code": test_code or None, "reference_files": data.get("reference_files") or [],
                "kind": "tests" if test_code else "file" if has_expected else "rubric",
                "test_count": data.get("test_count"), "category": data.get("category"),
                "generator_difficulty": data.get("generator_difficulty"),
                "reference_solution": data.get("reference_solution"),
                "split": data.get("split", "train"), "source_run_id": data.get("source_run_id"),
                "source_feedback_id": data.get("source_feedback_id"), "created_at": now()}
        if case["split"] not in {"train", "validation", "holdout"}:
            raise PlatformError("Unknown case split.", 422)
        case["revision"] = hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()
        return self.db.put("cases", case)

    def feedback(self, run_id, data):
        run = self.need("runs", run_id)
        if run["source"] == "eval":
            raise PlatformError("Evaluation runs cannot become production feedback.", 422)
        if run["status"] != "completed":
            raise PlatformError("Wait for a completed run before adding a quality correction.", 409)
        if run.get("session_id") and (data.get("test_code") or data.get("expected_name")):
            raise PlatformError("Chat feedback is saved for review. Conversation-based evaluations are not implemented; use a one-shot run for a regression case.", 422)
        f = {"id": new_id("fb"), "run_id": run_id, "agent_id": run["agent_id"], "rating": data["rating"],
             "correction": data.get("correction", ""), "created_at": now(), "case_id": None,
             "status": "needs_context" if run.get("session_id") else "needs_label"}
        expected = data.get("expected_content")
        if (expected is not None and data.get("expected_name")) or data.get("test_code"):
            if not f["correction"].strip():
                raise PlatformError("Explain the correction before saving a regression check.", 422)
            c = self.add_case(run["agent_id"], {"name": f"Correction · {run_id[-6:]}", "prompt": run["prompt"],
                "files": run["files"], "expected_name": data.get("expected_name"), "expected_content": expected,
                "test_code": data.get("test_code"),
                "source_run_id": run_id, "source_feedback_id": f["id"]})
            f.update(case_id=c["id"], status="eligible")
            artifacts = self.db.all("artifacts", run_id=run_id)
            if c.get("expected_name") and not c.get("test_code"):
                judged = evaluate_output([a for a in artifacts if a["role"] == "output"], c["expected_name"], expected)
                self.db.update("runs", run_id, outcome="passed" if judged["passed"] else "failed")
        self.db.put("feedback", f)
        self.db.event(run_id, "feedback.recorded", {"feedback_id": f["id"], "rating": f["rating"], "correction": f["correction"], "status": f["status"]})
        self.spawn(self.maybe_auto(run["agent_id"]))
        return f

    def pending_evidence(self, agent_id):
        consumed = set()
        for batch in self.db.all("batches", agent_id=agent_id):
            if batch["status"] not in {"error", "interrupted", "budget_exceeded"}:
                consumed.update(batch.get("evidence_ids", []))
        # Newest reviewed correction supersedes older corrections for the same run.
        newest, seen_runs = [], set()
        for f in self.db.all("feedback", agent_id=agent_id):
            if f["status"] != "eligible":
                continue
            if f["run_id"] in seen_runs:
                continue
            seen_runs.add(f["run_id"])
            if f["status"] == "eligible" and f["id"] not in consumed:
                newest.append(f)
        return newest

    async def maybe_auto(self, agent_id):
        if self.shutting_down:
            return
        agent = self.need("agents", agent_id)
        if not agent["auto_improve"]:
            return
        if any(b["status"] in ACTIVE_BATCH for b in self.db.all("batches", agent_id=agent_id)):
            return
        if len(self.pending_evidence(agent_id)) < int(os.getenv("BATCH_THRESHOLD", "3")):
            return
        batches = self.db.all("batches", agent_id=agent_id)
        from datetime import datetime, timezone
        cooldown = int(os.getenv("BATCH_COOLDOWN_SECONDS", "60"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(batches[0]["created_at"])).total_seconds() if batches else cooldown
        if age < cooldown:
            if agent_id not in self.timers:
                async def delayed():
                    try:
                        await asyncio.sleep(cooldown-age)
                    finally:
                        self.timers.pop(agent_id, None)
                    await self.maybe_auto(agent_id)
                self.timers[agent_id] = self.spawn(delayed())
            return
        # Error/budget batches require an explicit retry, not a new automatic loop.
        if batches and batches[0]["status"] in {"error", "interrupted", "budget_exceeded"}:
            return
        try:
            await self.start_batch(agent_id, "automatic")
        except PlatformError:
            pass

    async def start_batch(self, agent_id, trigger="manual"):
        self.check_ready()
        async with self.batch_lock:
            self.need("agents", agent_id)
            if any(b["status"] in ACTIVE_BATCH for b in self.db.all("batches", agent_id=agent_id)):
                raise PlatformError("Review the existing candidate or wait for the active batch.", 409)
            evidence = self.pending_evidence(agent_id)
            parent = self.current_version(agent_id)
            if self.need("agents", agent_id)["current_version_id"] != parent["id"]:
                raise PlatformError("Publish the current draft before improving the deployed version.", 409)
            # Superseded correction cases are excluded, while seed regressions remain.
            superseded = set()
            seen = set()
            for f in self.db.all("feedback", agent_id=agent_id):
                if not f.get("case_id"):
                    continue
                if f["run_id"] in seen and f.get("case_id"):
                    superseded.add(f["case_id"])
                seen.add(f["run_id"])
            cases = [c for c in self.db.all("cases", agent_id=agent_id) if c["split"] != "holdout" and c["id"] not in superseded]
            if not cases:
                raise PlatformError("Generate or add eval cases (or correct a run) before improving.", 422)
            candidate_count = max(1, min(5, int(os.getenv("BATCH_CANDIDATES", "3"))))
            max_runs = int(os.getenv("BATCH_MAX_RUNS", "250"))
            confirm_runs = max(0, min(5, int(os.getenv("BATCH_CONFIRM_RUNS", "2")))) if os.getenv("BATCH_CONFIRM", "1") == "1" else 0
            confirmation_budget = sum(c["split"] == "validation" for c in cases) * 2 * confirm_runs
            needed = len(cases) * (1 + candidate_count) + confirmation_budget
            if needed > max_runs:
                raise PlatformError(f"This suite needs up to {needed} runs ({len(cases)} cases x {1 + candidate_count} versions, plus {confirmation_budget} confirmation runs), above the {max_runs}-run batch limit. Increase BATCH_MAX_RUNS or lower BATCH_CANDIDATES.", 422)
            agent = self.need("agents", agent_id)
            b = {"id": new_id("batch"), "agent_id": agent_id, "parent_version_id": parent["id"], "candidate_version_id": None,
                 "status": "queued", "trigger": trigger, "created_at": now(), "finished_at": None, "error": None,
                 "evidence_ids": [f["id"] for f in evidence], "case_ids": [c["id"] for c in cases],
                 "suite_revision": hashlib.sha256(json.dumps([(c["id"], c["revision"]) for c in cases]).encode()).hexdigest(),
                 "old_playbook": parent["playbook"], "new_playbook": "", "rationale": "", "results": [], "summary": {},
                 "cost_usd": None, "max_cost_usd": float(os.getenv("BATCH_MAX_COST_USD", "5")), "run_count": 0,
                 "max_runs": max_runs, "timeout_s": int(os.getenv("BATCH_TIMEOUT_SECONDS", "2400")),
                 "candidate_count": candidate_count, "confirm_flips": os.getenv("BATCH_CONFIRM", "1") == "1",
                 "confirm_runs": confirm_runs, "planned_max_runs": needed,
                 "reward_config": {"cost_weight": float(agent.get("cost_weight", DEFAULT_COST_WEIGHT)),
                                   "latency_weight": float(agent.get("latency_weight", DEFAULT_LATENCY_WEIGHT))},
                 "candidates": [], "parent_metrics": {}, "parent_model": parent["model"], "parent_max_turns": parent.get("max_turns")}
            self.db.put("batches", b)
            self.spawn(self.process_batch(b, parent, evidence, cases))
            return b

    async def grade(self, case, record, artifacts, add_cost=None):
        """Score one completed run: deterministic checks gate, then the LLM judge for rubric criteria."""
        result = {"passed": True, "score": 1.0, "error": None, "details": {}}
        if case.get("test_code"):
            outputs = []
            for a in artifacts:
                try:
                    outputs.append({"name": a["name"], "content": Path(a["path"]).read_bytes()[:400_000].decode("utf-8", "replace")})
                except OSError:
                    continue
            work_dir = self.db.data_dir / "grading" / record["id"] / "workspace"
            async with self.eval_semaphore:
                tested = await runtime.run_tests(case["files"], outputs, case["test_code"], work_dir)
            result = {"passed": tested["total"] > 0 and tested["passed"] == tested["total"], "score": tested["score"],
                      "error": tested["error"], "details": {"code": "tests", "tests_passed": tested["passed"],
                                                            "tests_total": tested["total"], "tests": tested["results"]}}
            if not result["passed"]:
                return result
        if case.get("expected_name"):
            checked = evaluate_output(artifacts, case["expected_name"], case["expected_content"])
            if checked["details"].get("code") == "invalid_expectation":
                raise RuntimeError(f"Invalid case expectation: {checked['error']}")
            result = {**checked, "score": 1.0 if checked["passed"] else 0.0,
                      "details": {**result.get("details", {}), **checked.get("details", {})}}
            if not checked["passed"]:
                return result  # A failed hard check is final; skip judge spend.
        if case.get("rubric"):
            outputs = []
            for a in artifacts[:8]:
                try:
                    outputs.append({"name": a["name"], "content": Path(a["path"]).read_bytes()[:20000].decode("utf-8", "replace")})
                except OSError:
                    continue
            async def emit(kind, payload):
                self.db.event(record["id"], f"judge.{kind}", scrub(payload))
            verdict = await runtime.judge_output(record["config"], case["rubric"], case["prompt"], record.get("output") or "", outputs, emit)
            if add_cost:
                add_cost(verdict)
            unmet = [v["criterion"] for v in verdict["verdicts"] if not v["met"]]
            result = {"passed": result["passed"] and verdict["passed"], "score": verdict["score"],
                      "error": ("Judge: unmet criteria: " + "; ".join(unmet))[:1000] if unmet else None,
                      "details": {**result.get("details", {}), "judge_model": verdict.get("model"), "judge": verdict["verdicts"]}}
        return result

    async def process_batch(self, b, parent, evidence, cases):
        """One hill-climbing step: score parent, propose N candidates, score them, confirm flips, pick by reward."""
        bid = b["id"]
        agent_id = parent["agent_id"]
        weights = b["reward_config"]
        observed_cost = 0.0
        cost_known = True
        count = 0
        trials: dict[str, dict[str, list]] = {}  # version_id -> case_id -> trial results
        attempt_counts = {}

        def add_cost(result):
            nonlocal observed_cost, cost_known
            if result.get("cost_usd") is None:
                cost_known = False
            else:
                observed_cost += result["cost_usd"]
            self.db.update("batches", bid, cost_usd=observed_cost if cost_known else None, known_cost_usd=observed_cost)
            if observed_cost > b["max_cost_usd"]:
                raise PlatformError("Batch spending limit reached.")

        async def trial(version, case, phase):
            nonlocal count
            if count >= b["max_runs"]:
                raise PlatformError("Batch run limit reached.")
            count += 1
            self.db.update("batches", bid, run_count=count)
            run = self.prepare_run(version, case["prompt"], case["files"], "eval", batch_id=bid, case_id=case["id"])
            key = (version['id'], case['id'])
            attempt_counts[key] = attempt_counts.get(key, 0) + 1
            run = self.db.update('runs', run['id'], evaluation_context={
                'case_name': case['name'], 'case_split': case['split'],
                'phase': phase, 'attempt': attempt_counts[key]})
            record, artifacts = await self.execute(run)
            add_cost(record)
            if record["status"] != "completed":
                if record.get("error_category") not in runtime.AGENT_FAILURE_CATEGORIES:
                    raise RuntimeError(f"Evaluation incomplete (infrastructure): {record.get('error') or record['status']}")
                # The agent ran out of turns/time or produced nothing usable: a failed attempt, not an outage.
                result = {"passed": False, "score": 0.0, "error": f"Agent did not finish: {record.get('error')}"[:1000], "details": {"code": "agent_failure"}}
            else:
                result = await self.grade(case, record, artifacts, add_cost)
            self.db.update("runs", record["id"], outcome="passed" if result["passed"] else "failed")
            self.db.event(record["id"], "evaluation.result", {"passed": result["passed"], "score": result.get("score"), "error": result.get("error"), "case_id": case["id"]})
            self.db.put("eval_results", {"id": new_id("eval"), "batch_id": bid, "run_id": record["id"], "case_id": case["id"], "version_id": version["id"], **result})
            entry = {"passed": result["passed"], "score": result.get("score", 1.0 if result["passed"] else 0.0),
                     "error": result.get("error"), "run_id": record["id"],
                     "cost_usd": record.get("cost_usd"), "latency_ms": record.get("latency_ms")}
            trials.setdefault(version["id"], {}).setdefault(case["id"], []).append(entry)
            return entry

        async def run_all(jobs, phase='initial'):
            tasks = [asyncio.create_task(trial(version, case, phase)) for version, case in jobs]
            try:
                return await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        def rate(version_id, case_id):
            values = [t["score"] for t in trials.get(version_id, {}).get(case_id, [])]
            return sum(values) / len(values) if values else 0.0

        def metrics(version_id):
            by_case = trials.get(version_id, {})
            flat = [t for case in cases for t in by_case.get(case["id"], [])]
            # Score = mean partial credit across cases (fraction of hidden tests passed, 0/1 for file checks).
            pass_rate = _mean([rate(version_id, c["id"]) for c in cases]) or 0.0
            full = _mean([1.0 if all(t["passed"] for t in by_case.get(c["id"], [])) and by_case.get(c["id"]) else 0.0 for c in cases]) or 0.0
            costs = [t["cost_usd"] for t in flat]
            # Match the score's equal weighting of cases, even when validation is repeated.
            cost = _mean([_mean([t["cost_usd"] for t in by_case.get(c["id"], [])]) for c in cases]) if costs and all(v is not None for v in costs) else None
            latency = _mean([_mean([t["latency_ms"] for t in by_case.get(c["id"], [])]) for c in cases])
            reward = pass_rate - weights["cost_weight"] * (cost or 0.0) - weights["latency_weight"] * ((latency or 0) / 1000)
            agent_failures = sum(1 for t in flat if (t.get("error") or "").startswith("Agent did not finish"))
            return {"pass_rate": round(pass_rate, 4), "full_pass_rate": round(full, 4), "agent_failures": agent_failures,
                    "cost_per_task_usd": cost, "cost_known": cost is not None,
                    "latency_ms": round(latency) if latency is not None else None, "reward": round(reward, 6),
                    "trials": len(flat)}

        def compare(candidate_id):
            rows = []
            for case in cases:
                p, c = rate(parent["id"], case["id"]), rate(candidate_id, case["id"])
                pt, ct = trials[parent["id"]][case["id"]], trials[candidate_id][case["id"]]
                rows.append({"case_id": case["id"], "name": case["name"], "split": case["split"],
                             "parent_passed": p == 1.0, "candidate_passed": c == 1.0,
                             "parent_rate": p, "candidate_rate": c, "trials": len(ct),
                             "parent_error": next((t["error"] for t in pt if t["error"]), None),
                             "candidate_error": next((t["error"] for t in ct if t["error"]), None),
                             "parent_run_id": pt[-1]["run_id"], "candidate_run_id": ct[-1]["run_id"],
                             "outcome": "fixed" if c - p > SCORE_EPSILON else "regressed" if p - c > SCORE_EPSILON
                                        else "unchanged_pass" if c == 1.0 else "unchanged_fail"})
            summary = {"fixed": sum(r["outcome"] == "fixed" for r in rows), "regressed": sum(r["outcome"] == "regressed" for r in rows),
                       "parent_passed": sum(r["parent_passed"] for r in rows), "candidate_passed": sum(r["candidate_passed"] for r in rows),
                       "total": len(rows)}
            return rows, summary

        try:
            async with asyncio.timeout(b["timeout_s"]):
                self.db.update("batches", bid, status="analyzing")
                await run_all([(parent, case) for case in cases])
                self.db.update("batches", bid, parent_metrics=metrics(parent["id"]))

                # Failures the improver may learn from: training split only. Validation stays hidden.
                failures = []
                signatures = {}
                for case in cases:
                    if case["split"] != "train":
                        continue
                    for t in trials[parent["id"]][case["id"]]:
                        if t["passed"]:
                            continue
                        sig = _failure_signature(t)
                        signatures.setdefault(sig, []).append(case["name"])
                        failures.append({"case": case["name"], "kind": case.get("kind"), "prompt": case["prompt"],
                                         "files": [{"name": f["name"], "content": _cut(f["content"], 4000)} for f in case["files"]],
                                         "expected_name": case.get("expected_name"),
                                         "expected_content": _cut(case.get("expected_content") or "", 4000),
                                         "hidden_tests": _cut(case.get("test_code") or "", 6000),
                                         "rubric": case.get("rubric") or [], "score": t["score"],
                                         "failure_signature": sig, "verifier_failure": t["error"],
                                         "trace": self.db.events(t["run_id"])[-20:]})
                evidence_payload = []
                for f in evidence:
                    case = self.need("cases", f["case_id"])
                    evidence_payload.append({"source_run_id": f["run_id"], "source_feedback_id": f["id"], "correction": f["correction"], "prompt": case["prompt"], "files": case["files"],
                                             "expected_name": case["expected_name"], "expected_content": case["expected_content"],
                                             "hidden_tests": _cut(case.get("test_code") or "", 6000),
                                             "trace": self.db.events(f["run_id"])[-20:]})
                if not failures and not evidence_payload:
                    self.db.update("batches", bid, status="no_gain", rationale="The current version passes every training case and there is no new feedback, so there is nothing to climb on.",
                                   summary={"fixed": 0, "regressed": 0, "total": len(cases)}, finished_at=now())
                    return

                self.db.update("batches", bid, status="proposing")
                async def proposal_emit(kind, payload):
                    # Proposal events live on the batch; do not count as production evidence.
                    current = self.need("batches", bid)
                    events = current.get("proposal_events", [])
                    self.db.update("batches", bid, proposal_events=events + [{"type": kind, "ts": now(), "payload": scrub(payload)}])
                agent_now = self.need("agents", agent_id)
                catalog = await runtime.climbable_models()
                allowed = climb_models(agent_now, catalog)
                by_id = {m["id"]: m for m in catalog}
                # Compact rows: id, $ per million input tokens, $ per million output tokens.
                context = {"allowed_models": [[m, by_id.get(m, {}).get("input_per_mtok"), by_id.get(m, {}).get("output_per_mtok")] for m in allowed],
                           "turn_limit_range": list(TURN_RANGE),
                           "reward_config": weights,
                           "parent_metrics": metrics(parent["id"]),
                           "prior_attempts": [{"status": old["status"], "candidates": [
                               {k: c.get(k) for k in ("model", "max_turns", "metrics", "reason", "playbook")}
                               for c in old.get("candidates", [])]} for old in self.db.all("batches", agent_id=agent_id)
                               if old["id"] != bid and old.get("candidates")][:3],
                           "failure_signatures": sorted(({"signature": k, "count": len(v), "cases": v[:5]} for k, v in signatures.items()),
                                                        key=lambda x: -x["count"])}
                proposal = await runtime.propose_playbook(parent, evidence_payload, proposal_emit, failures=failures,
                                                          count=b["candidate_count"], context=context)
                add_cost(proposal)
                proposals = proposal.get("candidates") or [{"playbook": proposal["playbook"], "rationale": proposal.get("rationale", "")}]
                versions = []
                for item in proposals[:b["candidate_count"]]:
                    changes = {"playbook": item["playbook"]}
                    if item.get("model") and item["model"] in allowed:
                        changes["model"] = item["model"]
                    if item.get("max_turns"):
                        changes["max_turns"] = max(TURN_RANGE[0], min(TURN_RANGE[1], int(item["max_turns"])))
                    version = self.create_version(agent_id, {**parent, **changes}, created_by="improver",
                                                  parent_id=parent["id"], batch_id=bid, make_current=False)
                    versions.append((version, item))

                self.db.update("batches", bid, status="evaluating")
                # Each candidate is evaluated in isolation: a candidate whose newly chosen model
                # fails at the provider (unsupported API, outage) is disqualified, not the batch.
                broken = {}
                async def evaluate_candidate(version):
                    try:
                        await run_all([(version, case) for case in cases])
                    except RuntimeError as exc:
                        if version["model"] == parent["model"]:
                            raise
                        broken[version["id"]] = scrub(str(exc))[:400]
                await asyncio.gather(*(evaluate_candidate(v) for v, _ in versions))
                versions = [(v, item) for v, item in versions if v["id"] not in broken]
                broken_rows = [{"version_id": vid, "error": err} for vid, err in broken.items()]
                if not versions:
                    self.db.update("batches", bid, status="no_gain", candidates=[], broken_candidates=broken_rows,
                                   rationale="Every candidate failed to run on its chosen model.", finished_at=now())
                    return

                def score_candidate(version, item):
                    parent_m = metrics(parent["id"])
                    rows, summary = compare(version["id"])
                    m = metrics(version["id"])
                    cheaper = (m["cost_known"] and parent_m["cost_known"] and parent_m["cost_per_task_usd"]
                               and m["cost_per_task_usd"] <= parent_m["cost_per_task_usd"] * (1 - COST_IMPROVEMENT_MARGIN))
                    qualifies = summary["regressed"] == 0 and (summary["fixed"] > 0 or (m["pass_rate"] >= parent_m["pass_rate"] and m["pass_rate"] > 0 and bool(cheaper)))
                    reward_known = (not weights["cost_weight"] or (m["cost_known"] and parent_m["cost_known"]))
                    qualifies = qualifies and reward_known and m["reward"] > parent_m["reward"]
                    return {"version_id": version["id"], "playbook": item["playbook"], "rationale": item.get("rationale", ""),
                            "model": version["model"], "max_turns": version["max_turns"],
                            "summary": summary, "metrics": m, "qualifies": qualifies, "results": rows, "confirmed": False,
                            "reason": "fixed cases with no regressions" if qualifies and summary["fixed"] else
                                      "same quality at lower cost" if qualifies else
                                      "regressed a passing case" if summary["regressed"] else
                                      "cost unavailable for reward comparison" if not reward_known else
                                      "reward did not improve" if m["reward"] <= parent_m["reward"] else "no measured gain"}

                # 1. Every candidate is scored once on every case; pick the strongest by reward.
                scored = [score_candidate(v, item) for v, item in versions]
                pool = [c for c in scored if c["qualifies"]] or scored
                best = max(pool, key=lambda c: (c["metrics"]["reward"], c["summary"]["fixed"] - c["summary"]["regressed"]))

                # 2. Confirm only the finalist: it and the parent run every validation case
                #    BATCH_CONFIRM_RUNS more times, and the gate is re-checked on the averages.
                #    Every attempt counts; this measures consistency, it does not retry until success.
                #    Cost is predictable: validation cases x 2 versions x extra runs.
                validation = [c for c in cases if c["split"] == "validation"]
                extra = b.get("confirm_runs", int(os.getenv("BATCH_CONFIRM_RUNS", "2"))) if b["confirm_flips"] else 0
                if best["qualifies"] and validation and extra:
                    finalist = next(v for v, _ in versions if v["id"] == best["version_id"])
                    jobs = [(version, case) for _ in range(extra) for version in (parent, finalist) for case in validation]
                    if count + len(jobs) <= b["max_runs"]:
                        self.db.update("batches", bid, status="confirming", finalist_version_id=finalist["id"])
                        await run_all(jobs, phase='confirmation')
                        item = next(i for v, i in versions if v["id"] == finalist["id"])
                        confirmed = score_candidate(finalist, item)
                        confirmed["confirmed"] = True
                        confirmed["confirmation"] = {"validation_cases": len(validation), "runs_per_version": 1 + extra,
                                                     "single_run_reason": best["reason"]}
                        if not confirmed["qualifies"]:
                            confirmed["reason"] = "did not hold up on repeated validation runs"
                        scored = [confirmed if c["version_id"] == finalist["id"] else c for c in scored]
                        best = confirmed
                    else:
                        best = {**best, "qualifies": False, "reason": "required confirmation exceeded the batch run limit",
                                "confirmation": {"skipped": "batch run limit"}}
                        scored = [best if c["version_id"] == best["version_id"] else c for c in scored]
                parent_metrics = metrics(parent["id"])
                qualifies = best["qualifies"]
                stale = (self.current_version(agent_id)["id"] != parent["id"] or
                         self.need("agents", agent_id)["current_version_id"] != parent["id"])
                self.db.update("batches", bid, parent_metrics=parent_metrics, broken_candidates=broken_rows,
                               candidates=[{k: v for k, v in c.items() if k != "results"} for c in scored],
                               candidate_version_id=best["version_id"], new_playbook=best["playbook"], rationale=best["rationale"],
                               results=best["results"], summary={**best["summary"], "reason": best["reason"]},
                               status="stale" if stale else "awaiting_review" if qualifies else "no_gain", finished_at=now())
                if qualifies and not stale and self.need("agents", agent_id).get("auto_promote"):
                    self.decide_batch(bid, "promote", automatic=True)
        except asyncio.CancelledError:
            self.db.update("batches", bid, status="interrupted", error="Batch interrupted. Evidence is available for explicit retry.", finished_at=now())
            raise
        except (TimeoutError, PlatformError) as exc:
            self.db.update("batches", bid, status="budget_exceeded", error=str(exc) or "Batch time limit reached.", finished_at=now())
        except Exception as exc:
            self.db.update("batches", bid, status="error", error=scrub(str(exc)), finished_at=now())
        finally:
            if not self.shutting_down:
                self.spawn(self.maybe_auto(agent_id))

    async def generate_evals(self, agent_id, count=8):
        """Plan categories, draft cases per category in parallel, keep only cases that validate by execution."""
        self.check_ready()
        agent = self.need("agents", agent_id)
        if any(g["status"] == "running" for g in self.db.all("generations", agent_id=agent_id)):
            raise PlatformError("Eval generation is already running for this agent.", 409)
        version = self.need("versions", agent["current_version_id"])
        job = {"id": new_id("gen"), "agent_id": agent_id, "status": "running", "stage": "planning", "requested": int(count),
               "categories": [], "drafted": 0, "created_case_ids": [], "rejected": [], "cost_usd": 0.0, "model": None,
               "created_at": now(), "finished_at": None, "error": None}
        self.db.put("generations", job)
        self.spawn(self._generate(job, agent, version))
        return job

    async def _validate_generated(self, gid, index, draft):
        """Oracle and do-nothing probes, run in fresh sandboxes. Returns case fields or raises ValueError."""
        kind = draft.get("kind")
        files = clean_files([{"name": f["name"], "content": f["content"]} for f in draft.get("files") or []])
        # Docker's --mount syntax is comma-separated, so path components must be plain.
        base = self.db.data_dir / "generations" / gid / re.sub(r"[^A-Za-z0-9_.-]+", "_", str(index))[:80]
        if kind == "tests":
            if not draft.get("test_code") or not draft.get("reference_files"):
                raise ValueError("Test case is missing hidden tests or a reference solution.")
            for f in draft["reference_files"]:
                clean_name(f["name"])
            async with self.eval_semaphore:
                oracle = await runtime.run_tests(files, draft["reference_files"], draft["test_code"], base / "oracle" / "workspace")
            if oracle["total"] < 2 or oracle["passed"] != oracle["total"]:
                raise ValueError(f"Reference solution does not pass its own tests ({oracle['passed']}/{oracle['total']}): {oracle['error'] or ''}"[:400])
            async with self.eval_semaphore:
                nop = await runtime.run_tests(files, [], draft["test_code"], base / "nop" / "workspace")
            if nop["total"] and nop["passed"] == nop["total"]:
                raise ValueError("Tests pass with no solution at all, so they cannot tell good work from none.")
            return {"files": files, "test_code": draft["test_code"], "reference_files": draft["reference_files"],
                    "test_count": oracle["total"], "rubric": draft.get("rubric") or []}
        if kind == "file":
            if Path(draft.get("expected_name") or "").suffix.lower() not in {".csv", ".json"}:
                raise ValueError("File cases must produce a .csv or .json output.")
            async with self.eval_semaphore:
                artifacts = await runtime.run_reference(files, draft["reference_solution"], base / "oracle" / "workspace")
            match = [a for a in artifacts if a["name"] == draft["expected_name"]]
            if not match:
                raise ValueError(f"Reference solution did not write outputs/{draft['expected_name']}.")
            expected = Path(match[0]["path"]).read_text(encoding="utf-8")
            if any(f["content"].strip() == expected.strip() for f in files) or not expected.strip():
                raise ValueError("Expected output equals an input file or is empty, so doing nothing would pass.")
            return {"files": files, "expected_name": draft["expected_name"], "expected_content": expected,
                    "reference_solution": draft["reference_solution"], "rubric": draft.get("rubric") or []}
        if kind == "rubric":
            if not draft.get("rubric"):
                raise ValueError("Rubric case has no criteria.")
            return {"files": files, "rubric": draft["rubric"]}
        raise ValueError(f"Unknown case kind {kind!r}.")

    async def _generate(self, job, agent, version):
        import difflib
        import math
        gid, agent_id = job["id"], agent["id"]
        splits = ["train", "train", "validation", "train", "holdout"]  # 60% train, 20% validation, 20% held out
        lock = asyncio.Lock()
        cost = 0.0
        async def emit(kind, payload):
            pass  # Generator calls are summarized on the job; they have no run trace.
        def spend(result):
            nonlocal cost
            cost += result.get("cost_usd") or 0.0
            self.db.update("generations", gid, cost_usd=round(cost, 6))
        try:
            description = agent.get("description") or agent["name"]
            # Short demo suites favor smaller parallel drafts to reduce the longest call.
            per_category = 2 if job["requested"] <= 8 else 5
            n_categories = max(1, min(8, math.ceil(job["requested"] / per_category)))
            plan = await runtime.plan_eval_categories(version, description, n_categories, emit)
            spend(plan)
            categories = plan["categories"]
            self.db.update("generations", gid, stage="drafting", categories=categories, model=plan.get("model"))

            created, rejected = [], []
            # Deduplicate against the agent's existing cases too, so top-up generations add new coverage.
            kept_prompts = [re.sub(r"\s+", " ", c["prompt"].lower()) for c in self.db.all("cases", agent_id=agent_id)]
            async def one_category(category, quota):
                drafted = None
                for attempt_quota in (quota, max(1, quota // 2)):
                    try:
                        drafted = await runtime.generate_cases(version, description, attempt_quota, emit, category=category)
                        break
                    except Exception as exc:
                        # Usually the output limit: retry once with half as many cases.
                        if attempt_quota == max(1, quota // 2):
                            async with lock:
                                rejected.append({"name": f"category {category['name']}", "reason": scrub(str(exc) or type(exc).__name__)[:400]})
                            return
                spend(drafted)
                async with lock:
                    job_now = self.need("generations", gid)
                    self.db.update("generations", gid, drafted=job_now.get("drafted", 0) + len(drafted["cases"]), stage="validating")
                async def one_case(index, draft):
                    try:
                        prompt_key = re.sub(r"\s+", " ", draft["prompt"].lower())
                        async with lock:
                            if any(difflib.SequenceMatcher(None, prompt_key, k).ratio() > 0.9 for k in kept_prompts):
                                raise ValueError("Near-duplicate of another case.")
                            kept_prompts.append(prompt_key)
                        fields = await self._validate_generated(gid, f"{category['name']}-{index}", draft)
                        async with lock:
                            case = self.add_case(agent_id, {"name": draft["name"], "prompt": draft["prompt"], **fields,
                                                            "tags": draft.get("tags") or [], "category": category["name"],
                                                            "generator_difficulty": draft.get("difficulty"),
                                                            "split": splits[len(created) % len(splits)], "origin": "generated"})
                            created.append(case["id"])
                            self.db.update("generations", gid, created_case_ids=list(created))
                    except Exception as exc:
                        async with lock:
                            rejected.append({"name": str(draft.get("name", "case"))[:120], "category": category["name"],
                                             "reason": scrub(str(exc) or type(exc).__name__)[:400]})
                            self.db.update("generations", gid, rejected=list(rejected))
                await asyncio.gather(*(one_case(i, d) for i, d in enumerate(drafted["cases"])))

            quotas = [job["requested"] // len(categories) + (1 if i < job["requested"] % len(categories) else 0) for i in range(len(categories))]
            await asyncio.gather(*(one_category(c, q) for c, q in zip(categories, quotas) if q > 0))
            self.db.update("generations", gid, status="completed", stage="done", finished_at=now(),
                           created_case_ids=created, rejected=rejected)
            if created and self.need("agents", agent_id).get("auto_improve"):
                try:
                    batch = await self.start_batch(agent_id, "automatic")
                    self.db.update("generations", gid, batch_id=batch["id"])
                except PlatformError as exc:
                    # Surface why the automatic climb did not start (for example a batch run limit).
                    self.db.update("generations", gid, climb_error=exc.detail)
        except Exception as exc:
            self.db.update("generations", gid, status="error", stage="done", error=scrub(str(exc))[:1000], finished_at=now())

    @serialized
    def decide_batch(self, identity, action, automatic=False):
        b = self.need("batches", identity)
        if b["status"] != "awaiting_review":
            raise PlatformError("Only a candidate awaiting review can be promoted or rejected.", 409)
        if action == "reject":
            result = self.db.update("batches", identity, status="rejected", reviewed_at=now())
        else:
            current = self.current_version(b["agent_id"])
            if current["id"] != b["parent_version_id"] or self.need("agents", b["agent_id"])["current_version_id"] != b["parent_version_id"]:
                self.db.update("batches", identity, status="stale")
                raise PlatformError("The parent changed. Evaluate a new candidate before promotion.", 409)
            candidate = self.need("versions", b["candidate_version_id"])
            self.db.update("agents", b["agent_id"], current_version_id=candidate["id"])
            d = self.deployment(b["agent_id"])
            if d:
                self.db.update("deployments", d["id"], version_id=candidate["id"])
                self.db.put("deployment_changes", {"id": new_id("change"), "deployment_id": d["id"], "old_version_id": d["version_id"], "version_id": candidate["id"], "created_at": now()})
            result = self.db.update("batches", identity, status="promoted", reviewed_at=now(), promoted_by="automatic" if automatic else "user")
        self.spawn(self.maybe_auto(b["agent_id"]))
        return result
