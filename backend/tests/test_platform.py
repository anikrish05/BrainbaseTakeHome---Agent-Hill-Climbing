"""Service integration tests with explicit, deterministic runtime test doubles.

Nothing here seeds fabricated model results in the application/demo. Each test
uses its own temporary SQLite store; only model execution/proposals are mocked.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.app import runtime
from backend.app.service import Platform, PlatformError, clean_files
from backend.app.store import Store


EXPECTED = "value\n1\n"
WRONG = "value\n0\n"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("BATCH_THRESHOLD", "3")
    monkeypatch.setenv("BATCH_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("BATCH_MAX_RUNS", "32")
    monkeypatch.setenv("BATCH_MAX_COST_USD", "2")
    state = {"calls": [], "proposals": [], "outputs": {}, "block_prompt": None}

    monkeypatch.setattr(runtime, "get_runtime_status", lambda: {
        "ready": True, "model": "test-only/model", "provider": "openai",
    })

    async def execute(config, prompt, files, work_dir, emit):
        state["calls"].append({"version_id": config["id"], "prompt": prompt, "files": files})
        if prompt == state["block_prompt"]:
            state["entered"].set()
            await state["release"].wait()
        candidate = bool(config["playbook"])
        content = state["outputs"].get((candidate, prompt), EXPECTED if candidate else WRONG)
        await emit("model.start", {"model": "test-only/model"})
        await emit("tool.end", {"name": "write_file", "status": "completed"})
        output = Path(work_dir) / "clean.csv"
        output.write_text(content, encoding="utf-8")
        return {"output": "Explicit deterministic test double", "artifacts": [{"name": "clean.csv", "path": str(output)}],
                "cost_usd": 0.01, "cost_kind": "actual", "tool_calls": 1, "tokens_in": 10, "tokens_out": 10}

    async def propose(parent, evidence, emit, failures=None, count=1, context=None):
        state["proposals"].append(evidence)
        state.setdefault("failures", []).append(failures or [])
        await emit("model.end", {"output": "Explicit deterministic test proposal"})
        return {"playbook": "Test-only candidate rule", "rationale": "Deterministic lifecycle test", "cost_usd": 0.01}

    monkeypatch.setattr(runtime, "execute_run", execute)
    monkeypatch.setattr(runtime, "propose_playbook", propose)
    platform = Platform(Store(tmp_path / "data"))
    agent = platform.create_agent({"name": "Test Agent", "instructions": "Test-only instructions"})
    return platform, agent, state


async def drain(platform):
    """Finish finite service jobs, including jobs enqueued by their completion."""
    for _ in range(20):
        tasks = list(platform.tasks)
        if not tasks:
            return
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        await asyncio.sleep(0)
    raise AssertionError("Service kept enqueueing jobs after the test should have settled")


async def reviewed_run(platform, agent, prompt="learn", expected=EXPECTED):
    version = platform.need("versions", agent["current_version_id"])
    run = platform.prepare_run(version, prompt, [])
    record, _ = await platform.execute(run)
    assert record["status"] == "completed"
    feedback = platform.feedback(run["id"], {
        "rating": "negative", "correction": "The value must be 1.",
        "expected_name": "clean.csv", "expected_content": expected,
    })
    return record, feedback


async def candidate_batch(platform, agent):
    await reviewed_run(platform, agent)
    batch = await platform.start_batch(agent["id"])
    await drain(platform)
    return platform.need("batches", batch["id"])


def test_promotion_preserves_slug_key_and_accepted_run_version(harness):
    platform, agent, state = harness

    async def scenario():
        try:
            deployment = platform.deploy(agent["id"], "stable-agent")
            parent_id = deployment["version_id"]
            parent = platform.need("versions", parent_id)
            batch = await candidate_batch(platform, agent)
            assert batch["status"] == "awaiting_review"
            state.update(block_prompt="inflight", entered=asyncio.Event(), release=asyncio.Event())
            old_deployment = platform.authorize_deployment(deployment["slug"], deployment["token"])
            accepted = platform.submit_run(agent["id"], "inflight", [], "deployment", old_deployment)
            await asyncio.wait_for(state["entered"].wait(), timeout=2)

            platform.decide_batch(batch["id"], "promote")
            promoted = platform.authorize_deployment(deployment["slug"], deployment["token"])
            assert promoted["slug"] == "stable-agent"
            assert promoted["version_id"] == batch["candidate_version_id"]
            assert platform.need("versions", parent_id) == parent
            assert platform.need("runs", accepted["id"])["version_id"] == parent_id
            state["release"].set()
            await drain(platform)

            new_run = platform.submit_run(agent["id"], "after promotion", [], "deployment", promoted)
            await drain(platform)
            assert new_run["version_id"] == batch["candidate_version_id"]
            changes = platform.db.all("deployment_changes")
            assert changes[0]["old_version_id"] == parent_id
            assert changes[0]["version_id"] == batch["candidate_version_id"]
            assert platform.need("runs", accepted["id"])["status"] == "completed"
        finally:
            await platform.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("regression", [False, True])
def test_gate_rejects_no_gain_and_new_regressions(harness, regression):
    platform, agent, state = harness

    async def scenario():
        try:
            if regression:
                platform.add_case(agent["id"], {"name": "Preserve previous success", "prompt": "regression", "files": [],
                    "expected_name": "clean.csv", "expected_content": EXPECTED, "split": "validation"})
                state["outputs"][(False, "regression")] = EXPECTED
                state["outputs"][(True, "regression")] = WRONG
            else:
                state["outputs"][(True, "learn")] = WRONG
            batch = await candidate_batch(platform, agent)
            assert batch["status"] == "no_gain", batch.get("error")
            assert batch["summary"]["regressed"] == int(regression)
            assert batch["summary"]["fixed"] == int(regression)
            with pytest.raises(PlatformError) as error:
                platform.decide_batch(batch["id"], "promote")
            assert error.value.status == 409
            assert platform.current_version(agent["id"])["id"] == batch["parent_version_id"]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_changed_deployed_parent_makes_candidate_stale(harness):
    platform, agent, _ = harness

    async def scenario():
        try:
            platform.deploy(agent["id"], "test-stale")
            batch = await candidate_batch(platform, agent)
            assert batch["status"] == "awaiting_review"
            manual = platform.create_version(agent["id"], {"playbook": "A separately reviewed manual policy"})
            platform.deploy(agent["id"], version_id=manual["id"])
            with pytest.raises(PlatformError) as error:
                platform.decide_batch(batch["id"], "promote")
            assert error.value.status == 409
            assert platform.need("batches", batch["id"])["status"] == "stale"
            assert platform.current_version(agent["id"])["id"] == manual["id"]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_feedback_requires_checkable_correction_and_supersedes_same_run(harness):
    platform, agent, _ = harness

    async def scenario():
        try:
            run = platform.prepare_run(platform.current_version(agent["id"]), "learn", [])
            await platform.execute(run)
            unlabeled = platform.feedback(run["id"], {"rating": "positive", "correction": "Looks fine"})
            assert unlabeled["status"] == "needs_label"
            assert platform.pending_evidence(agent["id"]) == []
            assert platform.need("runs", run["id"])["outcome"] == "ungraded"
            with pytest.raises(PlatformError):
                platform.feedback(run["id"], {"rating": "negative", "expected_name": "clean.csv", "expected_content": EXPECTED})
            first = platform.feedback(run["id"], {"rating": "negative", "correction": "Use 1", "expected_name": "clean.csv", "expected_content": EXPECTED})
            second = platform.feedback(run["id"], {"rating": "negative", "correction": "Clarified: use 1", "expected_name": "clean.csv", "expected_content": EXPECTED})
            assert first["status"] == second["status"] == "eligible"
            assert [item["id"] for item in platform.pending_evidence(agent["id"])] == [second["id"]]
            assert platform.need("runs", run["id"])["outcome"] == "failed"
            await drain(platform)
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_eval_runs_cannot_recursively_become_feedback(harness):
    platform, agent, _ = harness

    async def scenario():
        try:
            run = platform.prepare_run(platform.current_version(agent["id"]), "evaluation only", [], source="eval")
            await platform.execute(run)
            with pytest.raises(PlatformError) as error:
                platform.feedback(run["id"], {"rating": "negative", "correction": "Use 1", "expected_name": "clean.csv", "expected_content": EXPECTED})
            assert error.value.status == 422
            assert not platform.db.all("feedback")
            assert not platform.db.all("cases")
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_identical_repeated_trials_really_execute_twice(harness):
    platform, agent, state = harness

    async def scenario():
        try:
            version = platform.current_version(agent["id"])
            first = platform.prepare_run(version, "repeat me", [], source="eval", case_id="same-case")
            second = platform.prepare_run(version, "repeat me", [], source="eval", case_id="same-case")
            assert first["id"] != second["id"]
            assert first["trial_id"] != second["trial_id"]
            await platform.execute(first)
            await platform.execute(second)
            assert len(state["calls"]) == 2
            assert len(platform.db.all("runs")) == 2
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_holdout_excluded_and_validation_not_given_to_improver(harness):
    platform, agent, state = harness

    async def scenario():
        try:
            validation = platform.add_case(agent["id"], {"name": "Hidden validation", "prompt": "validation-secret-prompt", "files": [],
                "expected_name": "clean.csv", "expected_content": EXPECTED, "split": "validation"})
            holdout = platform.add_case(agent["id"], {"name": "Untouched final", "prompt": "holdout-secret-prompt", "files": [],
                "expected_name": "clean.csv", "expected_content": EXPECTED, "split": "holdout"})
            batch = await candidate_batch(platform, agent)
            assert batch["status"] == "awaiting_review"
            assert validation["id"] in batch["case_ids"]
            assert holdout["id"] not in batch["case_ids"]
            assert all(call["prompt"] != "holdout-secret-prompt" for call in state["calls"])
            evidence = state["proposals"][0]
            assert [item["prompt"] for item in evidence] == ["learn"]
            # Suite failures shown to the improver come from the training split only.
            assert [item["prompt"] for item in state["failures"][0]] == ["learn"]
            # 2 cases x (parent + candidate) once, then the finalist and parent run the
            # validation case 2 more times each: 4 + 4 = 8.
            assert batch["run_count"] == 8
            assert len(platform.db.all("eval_results")) == 8
            trials = {row["split"]: row["trials"] for row in batch["results"]}
            assert trials == {"train": 1, "validation": 3}
            assert all(row["outcome"] == "fixed" for row in batch["results"])
            assert batch["candidates"][0]["confirmed"] is True
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_auto_threshold_counts_distinct_runs_and_starts_only_one_batch(harness):
    platform, agent, state = harness

    async def scenario():
        try:
            platform.db.update("agents", agent["id"], auto_improve=True)
            first_run, first_feedback = await reviewed_run(platform, agent, "one")
            await drain(platform)
            assert platform.db.all("batches") == []
            _, second_feedback = await reviewed_run(platform, agent, "two")
            newer_feedback = platform.feedback(first_run["id"], {
                "rating": "negative", "correction": "Reviewed replacement label", "expected_name": "clean.csv", "expected_content": EXPECTED,
            })
            await drain(platform)
            assert len(platform.pending_evidence(agent["id"])) == 2
            assert platform.db.all("batches") == []
            _, third_feedback = await reviewed_run(platform, agent, "three")
            await drain(platform)
            batches = platform.db.all("batches")
            assert len(batches) == 1
            batch = batches[0]
            assert batch["trigger"] == "automatic"
            assert batch["status"] == "awaiting_review"
            assert set(batch["evidence_ids"]) == {newer_feedback["id"], second_feedback["id"], third_feedback["id"]}
            assert first_feedback["id"] not in batch["evidence_ids"]
            assert len(batch["case_ids"]) == 3
            # 3 training cases x (parent + candidate); no validation cases, so nothing to confirm.
            assert batch["run_count"] == 6
            assert len(state["proposals"]) == 1
            await asyncio.gather(*(platform.maybe_auto(agent["id"]) for _ in range(4)))
            await drain(platform)
            assert len(platform.db.all("batches")) == 1
        finally:
            await platform.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("malformed", [
    {"expected_name": "clean.csv", "expected_content": "duplicate,duplicate\n1,2\n"},
    {"expected_name": "clean.json", "expected_content": '{"broken":'},
    {"expected_name": "nested/clean.csv", "expected_content": "value\n1\n"},
    {"expected_name": "clean.py", "expected_content": "print('not a data checker')"},
])
def test_invalid_case_expectations_never_enter_suite(harness, malformed):
    platform, agent, _ = harness
    with pytest.raises(PlatformError) as error:
        platform.add_case(agent["id"], {"name": "Invalid expectation", "prompt": "test", "files": [], **malformed})
    assert error.value.status == 422
    assert platform.db.all("cases") == []


@pytest.mark.parametrize("unsafe", ["outside", "symlink"])
def test_runtime_cannot_publish_output_outside_its_workspace(harness, tmp_path, monkeypatch, unsafe):
    platform, agent, _ = harness

    async def unsafe_execute(config, prompt, files, work_dir, emit):
        target = tmp_path / "outside.csv"
        target.write_text(EXPECTED)
        path = target
        if unsafe == "symlink":
            path = Path(work_dir) / "linked.csv"
            path.symlink_to(target)
        return {"artifacts": [{"name": "clean.csv", "path": str(path)}]}

    monkeypatch.setattr(runtime, "execute_run", unsafe_execute)

    async def scenario():
        try:
            run = platform.prepare_run(platform.current_version(agent["id"]), "malicious artifact", [])
            record, artifacts = await platform.execute(run)
            assert record["status"] == "failed"
            assert "unsafe" in record["error"].lower()
            assert artifacts == []
            assert platform.db.all("artifacts", run_id=run["id"]) == []
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_trace_persistence_and_restart_marks_unfinished_work_interrupted(harness):
    platform, agent, _ = harness
    run = platform.prepare_run(platform.current_version(agent["id"]), "interrupted task", [])
    platform.db.event(run["id"], "model.start", {"model": "test-only/model"})
    platform.db.event(run["id"], "tool.start", {"name": "bash"})
    reopened = Store(platform.db.data_dir)
    assert [event["seq"] for event in reopened.events(run["id"])] == [1, 2]
    assert [event["type"] for event in reopened.events(run["id"], after_seq=1)] == ["tool.start"]
    recovered = Platform(reopened)
    recovered.recover()
    assert reopened.get("runs", run["id"])["status"] == "interrupted"
    assert [event["seq"] for event in reopened.events(run["id"])] == [1, 2, 3]
    assert reopened.events(run["id"])[-1]["type"] == "run.error"


@pytest.mark.parametrize("name", ["../escape.csv", "/absolute.csv", "a/../../escape.csv", "a\\escape.csv", "a//b.csv"])
def test_input_path_traversal_is_rejected(name):
    with pytest.raises(PlatformError):
        clean_files([{"name": name, "content": "x"}])


def test_agent_failures_are_graded_failures_not_batch_errors(harness, monkeypatch):
    """A parent that runs out of turns fails its cases; the climb still completes."""
    platform, agent, state = harness
    from backend.app.runtime import RuntimeExecutionError
    original = runtime.execute_run

    async def flaky_parent(config, prompt, files, work_dir, emit):
        if not config["playbook"] and prompt == "learn":
            raise RuntimeExecutionError("Max turns (12) exceeded", category="agent", usage={"cost_usd": 0.01})
        return await original(config, prompt, files, work_dir, emit)

    async def scenario():
        try:
            await reviewed_run(platform, agent)
            monkeypatch.setattr(runtime, "execute_run", flaky_parent)
            batch = await platform.start_batch(agent["id"])
            await drain(platform)
            batch = platform.need("batches", batch["id"])
            assert batch["status"] == "awaiting_review"
            assert batch["summary"]["fixed"] == 1
            assert "Agent did not finish" in batch["results"][0]["parent_error"]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_infrastructure_failures_still_abort_the_comparison(harness, monkeypatch):
    platform, agent, state = harness
    from backend.app.runtime import RuntimeExecutionError

    async def outage(config, prompt, files, work_dir, emit):
        raise RuntimeExecutionError("Docker engine is unavailable", category="sandbox")

    async def scenario():
        try:
            await reviewed_run(platform, agent)
            monkeypatch.setattr(runtime, "execute_run", outage)
            batch = await platform.start_batch(agent["id"])
            await drain(platform)
            batch = platform.need("batches", batch["id"])
            assert batch["status"] == "error"
            assert "infrastructure" in batch["error"]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_best_of_several_candidates_wins_and_can_auto_promote(harness, monkeypatch):
    platform, agent, state = harness
    monkeypatch.setenv("BATCH_CANDIDATES", "3")

    async def propose(parent, evidence, emit, failures=None, count=1, context=None):
        return {"candidates": [{"playbook": f"rule {i}", "rationale": f"hypothesis {i}"} for i in range(count)], "cost_usd": 0.01}

    original = runtime.execute_run

    async def execute(config, prompt, files, work_dir, emit):
        # Only "rule 2" fixes the task; the other candidates behave like the parent.
        if config["playbook"] in {"rule 0", "rule 1"}:
            config = {**config, "playbook": ""}
        return await original(config, prompt, files, work_dir, emit)

    async def scenario():
        try:
            platform.db.update("agents", agent["id"], auto_promote=True)
            await reviewed_run(platform, agent)
            monkeypatch.setattr(runtime, "propose_playbook", propose)
            monkeypatch.setattr(runtime, "execute_run", execute)
            batch = await platform.start_batch(agent["id"])
            await drain(platform)
            batch = platform.need("batches", batch["id"])
            assert len(batch["candidates"]) == 3
            assert batch["new_playbook"] == "rule 2"
            assert [c["qualifies"] for c in batch["candidates"]] == [False, False, True]
            assert batch["status"] == "promoted" and batch["promoted_by"] == "automatic"
            assert platform.need("agents", agent["id"])["current_version_id"] == batch["candidate_version_id"]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_climb_runs_from_eval_suite_without_human_feedback(harness):
    platform, agent, state = harness

    async def scenario():
        try:
            platform.add_case(agent["id"], {"name": "Generated", "prompt": "suite-only", "files": [],
                "expected_name": "clean.csv", "expected_content": EXPECTED, "split": "train", "origin": "generated"})
            batch = await platform.start_batch(agent["id"])
            await drain(platform)
            batch = platform.need("batches", batch["id"])
            assert batch["evidence_ids"] == []
            assert batch["status"] == "awaiting_review"
            assert state["proposals"][0] == []
            assert state["failures"][0][0]["verifier_failure"]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_rubric_cases_are_graded_by_the_judge(harness, monkeypatch):
    platform, agent, state = harness
    judged = []

    async def judge(config, rubric, prompt, final_output, outputs, emit):
        judged.append(rubric)
        met = bool(config["playbook"])
        return {"passed": met, "score": 1.0 if met else 0.0, "cost_usd": 0.001, "model": "judge",
                "verdicts": [{"criterion": r, "met": met, "reason": "test"} for r in rubric]}

    async def scenario():
        try:
            monkeypatch.setattr(runtime, "judge_output", judge)
            platform.add_case(agent["id"], {"name": "Judged", "prompt": "summarize", "files": [],
                "rubric": ["The summary states how many rows were dropped."], "split": "train"})
            with pytest.raises(PlatformError):
                platform.add_case(agent["id"], {"name": "Empty", "prompt": "x", "files": []})
            batch = await platform.start_batch(agent["id"])
            await drain(platform)
            batch = platform.need("batches", batch["id"])
            assert judged and batch["status"] == "awaiting_review"
            assert "Judge: unmet criteria" in batch["results"][0]["parent_error"]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_generated_evals_are_validated_by_oracle_and_do_nothing_probes(harness, monkeypatch):
    platform, agent, state = harness
    good_tests = "from mod import f\ndef test_a():\n    assert f() == 1\ndef test_b():\n    assert f() + 1 == 2\n"
    base = {"kind": "tests", "prompt": "Write outputs/mod.py with f() returning 1.", "files": [],
            "test_code": good_tests, "reference_files": [{"name": "mod.py", "content": "def f():\n    return 1\n"}],
            "rubric": [], "tags": [], "difficulty": "medium"}

    async def plan(config, description, count, emit):
        return {"categories": [{"name": "basics", "description": "d", "kind": "tests"}], "cost_usd": 0.01, "model": "gen"}

    async def generate(config, description, count, emit, category=None):
        return {"cases": [
            {**base, "name": "good"},
            {**base, "name": "near duplicate", "prompt": base["prompt"] + " "},
            {**base, "name": "trivial", "prompt": "Totally different trivial request.", "test_code": "def test_x():\n    assert True\ndef test_y():\n    assert True\n"},
            {**base, "name": "broken reference", "prompt": "Another distinct request about g().", "reference_files": [{"name": "mod.py", "content": "BROKEN"}]},
            {**base, "name": "bad file kind", "kind": "file", "prompt": "Produce a python file output please.", "expected_name": "out.py", "reference_solution": "x"},
        ], "cost_usd": 0.02, "model": "gen"}

    async def run_tests(files, outputs, test_code, work_dir):
        if "assert True" in test_code:
            return {"total": 2, "passed": 2, "score": 1.0, "results": [], "error": None}
        ok = bool(outputs) and outputs[0]["content"] != "BROKEN"
        return {"total": 2, "passed": 2 if ok else 0, "score": 1.0 if ok else 0.0, "results": [], "error": None if ok else "ImportError"}

    async def scenario():
        try:
            monkeypatch.setattr(runtime, "plan_eval_categories", plan)
            monkeypatch.setattr(runtime, "generate_cases", generate)
            monkeypatch.setattr(runtime, "run_tests", run_tests)
            job = await platform.generate_evals(agent["id"], 5)
            await drain(platform)
            job = platform.need("generations", job["id"])
            assert job["status"] == "completed", job.get("error")
            assert len(job["created_case_ids"]) == 1
            reasons = {r["name"]: r["reason"] for r in job["rejected"]}
            assert set(reasons) == {"near duplicate", "trivial", "broken reference", "bad file kind"}
            assert "no solution" in reasons["trivial"]
            assert "does not pass its own tests" in reasons["broken reference"]
            case = platform.need("cases", job["created_case_ids"][0])
            assert case["kind"] == "tests" and case["test_count"] == 2 and case["origin"] == "generated"
            assert case["category"] == "basics" and "difficulty" not in case
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_hidden_test_cases_give_partial_credit_and_candidates_may_change_model_and_turns(harness, monkeypatch):
    platform, agent, state = harness
    monkeypatch.setenv("BATCH_CANDIDATES", "1")

    async def run_tests(files, outputs, test_code, work_dir):
        good = outputs and "fixed" in outputs[0]["content"]
        return {"total": 4, "passed": 4 if good else 2, "score": 1.0 if good else 0.5,
                "results": [], "error": None if good else "Failed tests: test_c: AssertionError"}

    async def execute(config, prompt, files, work_dir, emit):
        output = Path(work_dir) / "mod.py"
        output.write_text("fixed" if config["playbook"] else "draft", encoding="utf-8")
        return {"output": "ok", "artifacts": [{"name": "mod.py", "path": str(output)}],
                "cost_usd": 0.01, "cost_kind": "actual", "tool_calls": 1, "tokens_in": 1, "tokens_out": 1}

    async def propose(parent, evidence, emit, failures=None, count=1, context=None):
        state["context"] = context
        return {"candidates": [{"playbook": "rule", "rationale": "r", "model": "anthropic/claude-haiku-5.5", "max_turns": 99}], "cost_usd": 0.0}

    async def prices():
        return {}

    async def scenario():
        try:
            monkeypatch.setattr(runtime, "run_tests", run_tests)
            monkeypatch.setattr(runtime, "execute_run", execute)
            monkeypatch.setattr(runtime, "propose_playbook", propose)
            monkeypatch.setattr(runtime, "model_prices", prices)
            platform.add_case(agent["id"], {"name": "code", "prompt": "write mod", "files": [],
                "test_code": "def test_a():\n    assert True\n", "split": "train"})
            batch = await platform.start_batch(agent["id"])
            await drain(platform)
            batch = platform.need("batches", batch["id"])
            assert batch["status"] == "awaiting_review", batch.get("error")
            assert batch["parent_metrics"]["pass_rate"] == 0.5
            assert batch["candidates"][0]["metrics"]["pass_rate"] == 1.0
            assert batch["results"][0]["outcome"] == "fixed"
            candidate = platform.need("versions", batch["candidate_version_id"])
            assert candidate["model"] == "anthropic/claude-haiku-5.5"
            assert candidate["max_turns"] == 40 and candidate["timeout_s"] == 600
            assert state["context"]["failure_signatures"][0]["signature"] == "AssertionError"
            assert "anthropic/claude-haiku-5.5" in [row[0] for row in state["context"]["allowed_models"]]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_candidate_on_a_failing_model_is_disqualified_not_the_batch(harness, monkeypatch):
    platform, agent, state = harness
    monkeypatch.setenv("BATCH_CANDIDATES", "2")
    from backend.app.runtime import RuntimeExecutionError
    original = runtime.execute_run

    async def execute(config, prompt, files, work_dir, emit):
        if config["model"] == "broken/model":
            raise RuntimeExecutionError("Provider rejected the request", category="provider")
        return await original(config, prompt, files, work_dir, emit)

    async def propose(parent, evidence, emit, failures=None, count=1, context=None):
        return {"candidates": [{"playbook": "rule a", "rationale": "a", "model": "broken/model"},
                               {"playbook": "rule b", "rationale": "b"}], "cost_usd": 0.0}

    async def scenario():
        try:
            platform.db.update("agents", agent["id"], climb_models=["test-only/model", "broken/model"])
            await reviewed_run(platform, agent)
            monkeypatch.setattr(runtime, "execute_run", execute)
            monkeypatch.setattr(runtime, "propose_playbook", propose)
            batch = await platform.start_batch(agent["id"])
            await drain(platform)
            batch = platform.need("batches", batch["id"])
            assert batch["status"] == "awaiting_review", batch.get("error")
            assert len(batch["broken_candidates"]) == 1
            assert [c["playbook"] for c in batch["candidates"]] == ["rule b"]
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_finalist_that_fails_repeated_validation_runs_is_not_promoted(harness, monkeypatch):
    """A candidate that wins once by luck loses when the validation runs are repeated."""
    platform, agent, state = harness
    seen = {"candidate": 0, "parent": 0}
    original = runtime.execute_run

    async def execute(config, prompt, files, work_dir, emit):
        # On the validation case the candidate passes only its first attempt, while the
        # parent fails its first attempt and passes the two confirmation runs.
        if prompt == "validation-case":
            side = "candidate" if config["playbook"] else "parent"
            seen[side] += 1
            passes = seen[side] == 1 if side == "candidate" else seen[side] > 1
            config = {**config, "playbook": "pass" if passes else ""}
        return await original(config, prompt, files, work_dir, emit)

    async def scenario():
        try:
            platform.add_case(agent["id"], {"name": "Validation", "prompt": "validation-case", "files": [],
                "expected_name": "clean.csv", "expected_content": EXPECTED, "split": "validation"})
            state["outputs"][(True, "learn")] = WRONG  # the candidate does not fix the training case
            await reviewed_run(platform, agent)
            monkeypatch.setattr(runtime, "execute_run", execute)
            batch = await platform.start_batch(agent["id"])
            await drain(platform)
            batch = platform.need("batches", batch["id"])
            assert batch["status"] == "no_gain"
            assert batch["summary"]["reason"] == "did not hold up on repeated validation runs"
            row = next(r for r in batch["results"] if r["split"] == "validation")
            assert row["trials"] == 3 and abs(row["candidate_rate"] - 1 / 3) < 1e-9
        finally:
            await platform.close()

    asyncio.run(scenario())


def test_python_feedback_creates_checkable_evidence(harness):
    platform, agent, state = harness
    async def scenario():
        try:
            run = platform.submit_run(agent['id'], 'Write outputs/script.py', [])
            await drain(platform)
            tests = 'def test_empty():\n    from script import transform\n    assert transform([]) == []\n'
            feedback = platform.feedback(run['id'], {'rating': 'negative', 'correction': 'Handle empty inputs.', 'test_code': tests})
            assert feedback['status'] == 'eligible'
            case = platform.need('cases', feedback['case_id'])
            assert case['test_code'] == tests and case['source_run_id'] == run['id']
            assert case['kind'] == 'tests' and case['split'] == 'train'
            assert platform.pending_evidence(agent['id'])[0]['id'] == feedback['id']
            with pytest.raises(PlatformError):
                platform.feedback(run['id'], {'rating': 'negative', 'correction': 'Broken checker', 'test_code': 'def test_a(:'})
            assert len(platform.db.all('feedback')) == 1
        finally:
            await platform.close()
    asyncio.run(scenario())


def test_confirmation_budget_is_reserved_before_any_model_calls(harness, monkeypatch):
    platform, agent, state = harness
    monkeypatch.setenv('BATCH_CANDIDATES', '2')
    monkeypatch.setenv('BATCH_MAX_RUNS', '3')
    async def scenario():
        try:
            platform.add_case(agent['id'], {'name': 'validation', 'prompt': 'task', 'files': [], 'expected_name': 'clean.csv', 'expected_content': EXPECTED, 'split': 'validation'})
            with pytest.raises(PlatformError, match='plus 4 confirmation runs'):
                await platform.start_batch(agent['id'])
            assert not state['calls'] and not platform.db.all('batches')
        finally:
            await platform.close()
    asyncio.run(scenario())


def test_candidate_with_lower_reward_cannot_auto_promote(harness, monkeypatch):
    platform, agent, state = harness
    original = runtime.execute_run
    async def expensive_candidate(config, *args):
        result = await original(config, *args)
        result['cost_usd'] = 1.5 if config['playbook'] else 0.01
        return result
    async def scenario():
        try:
            platform.db.update('agents', agent['id'], auto_promote=True)
            await reviewed_run(platform, agent)
            monkeypatch.setattr(runtime, 'execute_run', expensive_candidate)
            batch = await platform.start_batch(agent['id'])
            await drain(platform)
            result = platform.need('batches', batch['id'])
            assert result['status'] == 'no_gain'
            assert result['summary']['fixed'] == 1
            assert result['summary']['reason'] == 'reward did not improve'
            assert platform.current_version(agent['id'])['id'] == agent['current_version_id']
        finally:
            await platform.close()
    asyncio.run(scenario())
