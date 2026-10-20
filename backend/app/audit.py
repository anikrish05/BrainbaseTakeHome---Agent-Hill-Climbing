"""One untouched final comparison per agent, separate from candidate selection.

Final audits consume held-out cases once and create fresh normal runtime trials.
The report is evidence for the walkthrough; it never changes a promotion gate,
proposes another playbook, feeds feedback, or triggers automatic improvement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from .evaluator import CHECKER_VERSION
from .runtime import AGENT_FAILURE_CATEGORIES
from .service import PlatformError, scrub
from .store import new_id, now


MAX_AUDIT_RUNS = 32
MAX_AUDIT_SECONDS = 600
MAX_AUDIT_COST_USD = 2.0


class _AuditBudget(Exception):
    pass


def _summary(results: list[dict], total: int) -> dict:
    paired = [row for row in results if row["parent_passed"] is not None and row["candidate_passed"] is not None]
    return {
        "total": total,
        "completed": len(paired),
        "incomplete": total - len(paired),
        "parent_graded": sum(row["parent_passed"] is not None for row in results),
        "candidate_graded": sum(row["candidate_passed"] is not None for row in results),
        "parent_passed": sum(row["parent_passed"] is True for row in results),
        "candidate_passed": sum(row["candidate_passed"] is True for row in results),
        "fixed": sum(not row["parent_passed"] and row["candidate_passed"] for row in paired),
        "regressed": sum(row["parent_passed"] and not row["candidate_passed"] for row in paired),
        "infrastructure_errors": sum(bool(row.get("infrastructure_error")) for row in results),
    }


async def start_audit(platform, agent_id: str, batch_id: str) -> dict:
    """Accept one final report per agent; repeated requests return that report.

    No await occurs between the one-report check and persistence, so concurrent
    requests in this process cannot claim multiple audits. Reports are retained
    after interruption/error too: reusing final cases for candidate selection
    would invalidate the stated untouched final comparison.
    """
    platform.need("agents", agent_id)
    batch = platform.need("batches", batch_id)
    if batch["agent_id"] != agent_id:
        raise PlatformError("The selected batch does not belong to this agent.", 422)
    existing = platform.db.all("audits", agent_id=agent_id)
    if existing:
        return existing[0]
    if batch["status"] not in {"awaiting_review", "promoted", "no_gain"} or not batch.get("candidate_version_id"):
        raise PlatformError("Select a completed comparison with a candidate for the final audit.", 409)
    platform.check_ready()
    parent = platform.need("versions", batch["parent_version_id"])
    candidate = platform.need("versions", batch["candidate_version_id"])
    if parent["agent_id"] != agent_id or candidate["agent_id"] != agent_id:
        raise PlatformError("Audit versions must belong to this agent.", 422)
    cases = platform.db.all("cases", agent_id=agent_id, split="holdout")
    if not cases:
        raise PlatformError("Add held-out cases before starting the final audit.", 422)
    if len(cases) * 2 > MAX_AUDIT_RUNS:
        raise PlatformError(f"Final audits support at most {MAX_AUDIT_RUNS // 2} held-out cases.", 422)
    job = {
        "id": new_id("audit"), "agent_id": agent_id, "batch_id": batch_id,
        "parent_version_id": parent["id"], "candidate_version_id": candidate["id"],
        "status": "queued", "created_at": now(), "finished_at": None,
        "case_ids": [case["id"] for case in cases],
        "suite_revision": hashlib.sha256(json.dumps([(case["id"], case["revision"]) for case in cases]).encode()).hexdigest(),
        "checker_version": CHECKER_VERSION, "results": [], "summary": _summary([], len(cases)),
        "run_count": 0, "max_runs": MAX_AUDIT_RUNS, "timeout_s": MAX_AUDIT_SECONDS,
        "cost_usd": None, "known_cost_usd": 0.0, "max_cost_usd": MAX_AUDIT_COST_USD,
        "error": None,
        "note": "One final audit per agent. Small-sample evidence; held-out results do not select candidates.",
    }
    platform.db.put("audits", job)
    platform.spawn(_process_audit(platform, job, parent, candidate, cases))
    return job


async def _process_audit(platform, job: dict, parent: dict, candidate: dict, cases: list[dict]) -> None:
    audit_id = job["id"]
    started = time.monotonic()
    results: list[dict] = []
    count = 0
    known_cost = 0.0
    all_cost_known = True

    def persist(**updates):
        platform.db.update(
            "audits", audit_id, results=results, summary=_summary(results, len(cases)),
            run_count=count, known_cost_usd=known_cost,
            cost_usd=known_cost if all_cost_known else None, **updates,
        )

    def add_cost(result):
        nonlocal known_cost, all_cost_known
        if result.get("cost_usd") is None:
            all_cost_known = False
        else:
            known_cost += result["cost_usd"]
        persist()
        if known_cost > job["max_cost_usd"]:
            raise _AuditBudget("Final audit observed spending limit reached.")

    async def evaluate(version, case, row, side):
        nonlocal count, known_cost, all_cost_known
        if count >= job["max_runs"]:
            raise _AuditBudget("Final audit run limit reached.")
        count += 1
        run = platform.prepare_run(version, case["prompt"], case["files"], source="eval", case_id=case["id"])
        run = platform.db.update("runs", run["id"], audit_id=audit_id, evaluation_context={
            'case_name': case['name'], 'case_split': 'holdout', 'phase': 'holdout', 'attempt': 1})
        row[f"{side}_run_id"] = run["id"]
        persist()
        record, artifacts = await platform.execute(run)
        add_cost(record)
        if record["status"] != "completed":
            if record.get("error_category") in AGENT_FAILURE_CATEGORIES:
                # The agent failed the task (turn/time limit, unusable output): a graded failure.
                result = {"passed": False, "score": 0.0, "error": f"Agent did not finish: {record.get('error')}"[:1000],
                          "details": {"code": "agent_failure"}}
            else:
                row[f"{side}_error"] = record.get("error") or record["status"]
                row["infrastructure_error"] = True
                persist()
                return
        else:
            try:
                result = await platform.grade(case, record, artifacts, add_cost)
            except _AuditBudget:
                raise
            except Exception as exc:  # Invalid expectation or judge outage: not the agent's fault.
                row[f"{side}_error"] = scrub(str(exc))[:1000]
                row["infrastructure_error"] = True
                persist()
                return
        row[f"{side}_passed"] = result["passed"]
        row[f"{side}_error"] = result.get("error")
        platform.db.update("runs", run["id"], outcome="passed" if result["passed"] else "failed")
        platform.db.event(run["id"], "evaluation.result", {
            "audit_id": audit_id, "case_id": case["id"], "passed": result["passed"], "error": result.get("error"),
        })
        platform.db.put("eval_results", {
            "id": new_id("eval"), "audit_id": audit_id, "batch_id": None,
            "run_id": run["id"], "case_id": case["id"], "case_revision": case["revision"],
            "version_id": version["id"], "trial_id": run["trial_id"], **result,
        })
        persist()

    try:
        persist(status="running")
        async with asyncio.timeout(job["timeout_s"]):
            for case in cases:
                row = {
                    "case_id": case["id"], "name": case["name"],
                    "parent_passed": None, "candidate_passed": None,
                    "parent_error": None, "candidate_error": None,
                    "parent_run_id": None, "candidate_run_id": None,
                    "infrastructure_error": False,
                }
                results.append(row)
                await evaluate(parent, case, row, "parent")
                await evaluate(candidate, case, row, "candidate")
            incomplete = any(row["infrastructure_error"] for row in results)
            persist(status="error" if incomplete else "completed", finished_at=now(),
                    error="Final comparison is incomplete due to infrastructure failures." if incomplete else None)
    except asyncio.CancelledError:
        persist(status="interrupted", finished_at=now(), error="Final audit interrupted; its partial report is retained.")
        raise
    except (TimeoutError, _AuditBudget) as exc:
        persist(status="budget_exceeded", finished_at=now(), error=str(exc) or "Final audit time limit reached.")
    except Exception as exc:
        persist(status="error", finished_at=now(), error=scrub(str(exc) or type(exc).__name__))
    finally:
        platform.db.update("audits", audit_id, latency_ms=round((time.monotonic() - started) * 1000))
