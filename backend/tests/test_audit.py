from __future__ import annotations

import asyncio

import pytest

from backend.app.audit import MAX_AUDIT_RUNS, start_audit
from backend.app.service import PlatformError
from backend.tests.test_platform import candidate_batch, drain, harness


def add_holdout(platform, agent, name="Untouched final case"):
    return platform.add_case(agent["id"], {"name": name, "prompt": f"final-{name}", "files": [],
        "expected_name": "clean.csv", "expected_content": "value\n1\n", "split": "holdout"})


def test_final_audit_runs_fresh_heldout_trials_once_and_never_improves(harness):
    platform, agent, state = harness

    async def scenario():
        try:
            holdout = add_holdout(platform, agent)
            batch = await candidate_batch(platform, agent)
            before_runs = platform.db.all("runs")
            before_feedback = platform.db.all("feedback")
            before_proposals = len(state["proposals"])
            first, repeated = await asyncio.gather(
                start_audit(platform, agent["id"], batch["id"]),
                start_audit(platform, agent["id"], batch["id"]),
            )
            assert first["id"] == repeated["id"]
            await drain(platform)
            report = platform.need("audits", first["id"])
            assert report["status"] == "completed"
            assert report["case_ids"] == [holdout["id"]]
            assert report["summary"]["fixed"] == 1
            assert report["summary"]["total"] == report["summary"]["completed"] == 1
            assert report["run_count"] == 2
            assert report["cost_usd"] == 0.02
            final_runs = [run for run in platform.db.all("runs") if run.get("audit_id") == report["id"]]
            assert len(final_runs) == 2
            assert {run["source"] for run in final_runs} == {"eval"}
            contexts = [platform.run_view(run['id'])['trace_context'] for run in final_runs]
            assert {c['role'] for c in contexts} == {'baseline', 'candidate'}
            assert all(c['kind'] == 'holdout' and c['phase'] == 'holdout' and c['case_split'] == 'holdout' for c in contexts)
            assert len({run["trial_id"] for run in before_runs + final_runs}) == len(before_runs) + 2
            assert platform.db.all("feedback") == before_feedback
            assert len(state["proposals"]) == before_proposals
            assert len(platform.db.all("batches")) == 1
            again = await start_audit(platform, agent["id"], batch["id"])
            await drain(platform)
            assert again["id"] == first["id"]
            assert len(platform.db.all("runs")) == len(before_runs) + 2
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_final_audit_requires_holdout_and_candidate_ownership(harness):
    platform, agent, _ = harness

    async def scenario():
        try:
            batch = await candidate_batch(platform, agent)
            with pytest.raises(PlatformError) as error:
                await start_audit(platform, agent["id"], batch["id"])
            assert error.value.status == 422
            assert platform.db.all("audits") == []
            other = platform.create_agent({"name": "Other", "instructions": "Test"})
            with pytest.raises(PlatformError) as error:
                await start_audit(platform, other["id"], batch["id"])
            assert error.value.status == 422
            for number in range(MAX_AUDIT_RUNS // 2 + 1):
                add_holdout(platform, agent, str(number))
            with pytest.raises(PlatformError) as error:
                await start_audit(platform, agent["id"], batch["id"])
            assert error.value.status == 422
            assert platform.db.all("audits") == []
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_final_audit_infrastructure_errors_are_not_task_failures(harness, monkeypatch):
    platform, agent, _ = harness

    async def scenario():
        try:
            holdout = add_holdout(platform, agent)
            batch = await candidate_batch(platform, agent)

            async def broken_execute(config, prompt, files, work_dir, emit):
                raise RuntimeError("Test-only provider outage")

            from backend.app import runtime
            monkeypatch.setattr(runtime, "execute_run", broken_execute)
            job = await start_audit(platform, agent["id"], batch["id"])
            await drain(platform)
            report = platform.need("audits", job["id"])
            assert report["status"] == "error"
            assert report["summary"]["infrastructure_errors"] == 1
            assert report["summary"]["completed"] == 0
            assert report["summary"]["parent_graded"] == report["summary"]["candidate_graded"] == 0
            assert report["results"][0]["case_id"] == holdout["id"]
            assert report["results"][0]["parent_passed"] is None
            assert report["results"][0]["candidate_passed"] is None
            assert "provider outage" in report["results"][0]["parent_error"]
            assert (await start_audit(platform, agent["id"], batch["id"]))["id"] == job["id"]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_final_audit_stops_on_observed_cost_budget(harness, monkeypatch):
    platform, agent, _ = harness

    async def scenario():
        try:
            add_holdout(platform, agent)
            batch = await candidate_batch(platform, agent)
            import backend.app.audit as audit
            monkeypatch.setattr(audit, "MAX_AUDIT_COST_USD", 0.005)
            job = await start_audit(platform, agent["id"], batch["id"])
            await drain(platform)
            report = platform.need("audits", job["id"])
            assert report["status"] == "budget_exceeded"
            assert report["run_count"] == 1
            assert report["known_cost_usd"] == 0.01
            assert report["summary"]["incomplete"] == 1
        finally:
            await platform.close()

    asyncio.run(scenario())
