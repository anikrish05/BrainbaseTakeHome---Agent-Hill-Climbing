"""HTTP contract tests; runtime/model calls are explicit test doubles only."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import runtime
from backend.app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BATCH_COOLDOWN_SECONDS", "0")
    monkeypatch.setattr(runtime, "get_runtime_status", lambda: {
        "ready": True, "model": "test-only/model", "provider": "openai",
    })
    monkeypatch.setattr(runtime, "get_models", lambda: [{"id": "test-only/model", "provider": "openai"}])

    async def execute(config, prompt, files, work_dir, emit, **kwargs):
        await emit("model.start", {"model": "test-only/model"})
        await emit("tool.end", {"name": "write_file", "status": "completed"})
        output = Path(work_dir) / "clean.csv"
        output.write_text("value\n1\n" if config["playbook"] else "value\n0\n")
        return {"output": "Explicit HTTP-test runtime double", "artifacts": [{"name": "clean.csv", "path": str(output)}],
                "cost_usd": 0.01, "cost_kind": "actual", "tool_calls": 1}

    async def propose(parent, evidence, emit, failures=None, count=1, context=None):
        return {"playbook": "HTTP-test candidate", "rationale": "Explicit test double", "cost_usd": 0.01}

    monkeypatch.setattr(runtime, "execute_run", execute)
    monkeypatch.setattr(runtime, "propose_playbook", propose)
    app = create_app(tmp_path / "data")
    with TestClient(app) as api:
        yield api


def create_agent(client, name="HTTP Test Agent"):
    response = client.post("/api/agents", json={"name": name, "instructions": "Test-only instruction"})
    assert response.status_code == 201, response.text
    return response.json()


def wait_for(client, path, terminal, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] in terminal:
            return record
        time.sleep(0.01)
    pytest.fail(f"Timed out waiting for {path}: {record}")


def complete_run(client, agent_id, prompt="test task"):
    response = client.post(f"/api/agents/{agent_id}/runs", json={"prompt": prompt, "files": []})
    assert response.status_code == 202, response.text
    return wait_for(client, f"/api/runs/{response.json()['id']}", {"completed", "failed"})


def test_create_seed_version_and_deploy_contracts(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/status").json()["ready"]
    agent = create_agent(client)
    original_version = agent["current_version"]["id"]
    response = client.post(f"/api/agents/{agent['id']}/versions", json={"playbook": "Manually reviewed rule"})
    assert response.status_code == 201
    assert response.json()["parent_id"] == original_version
    versions = client.get(f"/api/agents/{agent['id']}").json()["versions"]
    assert len(versions) == 2
    assert next(version for version in versions if version["id"] == original_version)["playbook"] == ""
    deployment = client.post(f"/api/agents/{agent['id']}/deploy", json={"slug": "http-test"})
    assert deployment.status_code == 200
    token = deployment.json()["token"]
    assert client.get("/api/deployments/http-test").status_code == 403
    shared = client.get("/api/deployments/http-test", headers={"X-Deployment-Key": token})
    assert shared.status_code == 200
    assert shared.json()["version_id"] == response.json()["id"]
    assert "token_hash" not in shared.json()

    first_seed = client.post("/api/demo/seed")
    second_seed = client.post("/api/demo/seed")
    assert first_seed.status_code == second_seed.status_code == 201
    assert first_seed.json()["id"] == second_seed.json()["id"]
    assert first_seed.json()["stats"]["runs"] == 0
    cases = client.get(f"/api/agents/{first_seed.json()['id']}/cases").json()
    assert len(cases) == 8
    assert [case["split"] for case in cases].count("holdout") == 2
    assert client.get(f"/api/agents/{first_seed.json()['id']}/runs").json() == []


def test_http_feedback_improvement_and_promotion_have_running_event_loop(client):
    agent = create_agent(client)
    deployment = client.post(f"/api/agents/{agent['id']}/deploy", json={"slug": "http-loop"}).json()
    run = complete_run(client, agent["id"])
    assert run["status"] == "completed"
    assert {event["type"] for event in run["events"]} >= {"run.start", "model.start", "tool.end", "run.end"}
    assert "config" not in run and "files" not in run
    assert all("path" not in artifact for artifact in run["artifacts"])
    feedback = client.post(f"/api/runs/{run['id']}/feedback", json={
        "rating": "negative", "correction": "Use one as the value.", "expected_name": "clean.csv", "expected_content": "value\n1\n",
    })
    assert feedback.status_code == 201, feedback.text
    assert feedback.json()["status"] == "eligible"
    assert client.get(f"/api/agents/{agent['id']}").json()["eligible_evidence"] == 1
    response = client.post(f"/api/agents/{agent['id']}/improvements")
    assert response.status_code == 202, response.text
    batch = wait_for(client, f"/api/improvements/{response.json()['id']}", {"awaiting_review", "error", "no_gain"})
    assert batch["status"] == "awaiting_review", batch
    assert batch["summary"]["fixed"] == 1
    promoted = client.post(f"/api/improvements/{batch['id']}/promote")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "promoted"
    shared = client.get(f"/api/deployments/http-loop?token={deployment['token']}")
    assert shared.status_code == 200
    assert shared.json()["version_id"] == batch["candidate_version_id"]
    assert client.patch(f"/api/agents/{agent['id']}", json={"auto_improve": True}).status_code == 200


def test_shared_run_tokens_are_scoped_to_deployment_and_artifacts_download(client):
    first = create_agent(client, "First")
    second = create_agent(client, "Second")
    first_deploy = client.post(f"/api/agents/{first['id']}/deploy", json={"slug": "first"}).json()
    second_deploy = client.post(f"/api/agents/{second['id']}/deploy", json={"slug": "second"}).json()
    assert client.post("/api/deployments/first/runs", json={"prompt": "task"}).status_code == 403
    assert client.post("/api/deployments/first/runs", json={"prompt": "task", "token": second_deploy["token"]}).status_code == 403
    invocation = client.post("/api/deployments/first/runs", json={"prompt": "task", "token": first_deploy["token"]})
    assert invocation.status_code == 202
    run = wait_for(client, f"/api/runs/{invocation.json()['id']}?token={first_deploy['token']}", {"completed", "failed"})
    assert run["status"] == "completed"
    assert client.get(f"/api/runs/{run['id']}?token={second_deploy['token']}").status_code == 403
    assert client.get(f"/api/runs/{run['id']}/events?token={second_deploy['token']}").status_code == 403
    artifact_url = run["artifacts"][0]["url"]
    assert client.get(f"{artifact_url}?token={second_deploy['token']}").status_code == 403
    output = client.get(f"{artifact_url}?token={first_deploy['token']}")
    assert output.status_code == 200
    assert output.text == "value\n0\n"
    assert output.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in output.headers["content-disposition"]
    playground = complete_run(client, first["id"])
    assert client.get(f"/api/runs/{playground['id']}?token={first_deploy['token']}").status_code == 403


def test_publish_and_rollback_preserve_key_until_explicit_rotation(client):
    agent = create_agent(client)
    initial = client.post(f"/api/agents/{agent['id']}/deploy", json={"slug": "stable-key"}).json()
    original_key = initial["token"]
    original_version = initial["version_id"]
    newer = client.post(f"/api/agents/{agent['id']}/versions", json={"playbook": "Reviewed second version"}).json()

    for version_id in (newer["id"], original_version):
        published = client.post(f"/api/agents/{agent['id']}/deploy", json={"version_id": version_id})
        assert published.status_code == 200, published.text
        assert published.json()["slug"] == "stable-key"
        assert published.json()["token"] is None
        assert published.json()["url"] == "/d/stable-key"
        shared = client.get("/api/deployments/stable-key", headers={"X-Deployment-Key": original_key})
        assert shared.status_code == 200
        assert shared.json()["version_id"] == version_id
        invoked = client.post("/api/deployments/stable-key/runs", headers={"X-Deployment-Key": original_key}, json={"prompt": "Verify published version"})
        assert invoked.status_code == 202, invoked.text
        assert invoked.json()["version_id"] == version_id
        assert wait_for(client, f"/api/runs/{invoked.json()['id']}", {"completed", "failed"})["status"] == "completed"

    rotated = client.post(f"/api/agents/{agent['id']}/deploy", json={"version_id": original_version, "rotate_key": True})
    assert rotated.status_code == 200, rotated.text
    rotated_key = rotated.json()["token"]
    assert rotated_key and rotated_key != original_key
    assert rotated.json()["slug"] == "stable-key"
    assert client.get("/api/deployments/stable-key", headers={"X-Deployment-Key": original_key}).status_code == 403
    assert client.post("/api/deployments/stable-key/runs", headers={"X-Deployment-Key": original_key}, json={"prompt": "Old key"}).status_code == 403
    authorized = client.get("/api/deployments/stable-key", headers={"X-Deployment-Key": rotated_key})
    assert authorized.status_code == 200
    assert authorized.json()["version_id"] == original_version


@pytest.mark.parametrize("name", ["../escape.csv", "/absolute.csv", "a\\b.csv", "bad\x00.csv", "bad\n.csv"])
def test_malformed_input_names_rejected_before_creating_run(client, name):
    agent = create_agent(client)
    response = client.post(f"/api/agents/{agent['id']}/runs", json={"prompt": "task", "files": [{"name": name, "content": "data"}]})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/agents/{agent['id']}/runs").json() == []


@pytest.mark.parametrize("case_patch", [
    {"expected_name": "bad\x00.csv"},
    {"expected_name": "../clean.csv"},
    {"expected_content": "a,a\n1,2\n"},
    {"expected_name": "clean.json", "expected_content": '{"a":'},
])
def test_malformed_cases_are_rejected(client, case_patch):
    agent = create_agent(client)
    payload = {"name": "case", "prompt": "task", "expected_name": "clean.csv", "expected_content": "a\n1\n", **case_patch}
    response = client.post(f"/api/agents/{agent['id']}/cases", json=payload)
    assert response.status_code == 422, response.text
    assert client.get(f"/api/agents/{agent['id']}/cases").json() == []


def test_feedback_pair_validation_and_invalid_expectation(client):
    agent = create_agent(client)
    run = complete_run(client, agent["id"])
    for extra in [
        {"expected_name": "clean.csv"},
        {"expected_content": "value\n1\n"},
        {"expected_name": "clean.json", "expected_content": "not json"},
    ]:
        response = client.post(f"/api/runs/{run['id']}/feedback", json={"rating": "negative", "correction": "Fix it", **extra})
        assert response.status_code == 422
    assert client.get(f"/api/runs/{run['id']}").json()["feedback"] is None


def test_combined_input_size_limit_and_file_count(client):
    agent = create_agent(client)
    too_many = [{"name": f"f{index}.csv", "content": "a\n1\n"} for index in range(11)]
    assert client.post(f"/api/agents/{agent['id']}/runs", json={"prompt": "task", "files": too_many}).status_code == 422
    oversized = [{"name": "first.csv", "content": "a" * 1_000_001}, {"name": "second.csv", "content": "b" * 1_000_001}]
    assert client.post(f"/api/agents/{agent['id']}/runs", json={"prompt": "task", "files": oversized}).status_code == 422
    assert client.get(f"/api/agents/{agent['id']}/runs").json() == []


def test_unpaired_unicode_in_input_is_a_validation_error(client):
    agent = create_agent(client)
    body = json.dumps({"prompt": "task", "files": [{"name": "input.csv", "content": "\ud800"}]})
    response = client.post(f"/api/agents/{agent['id']}/runs", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/agents/{agent['id']}/runs").json() == []


def test_http_python_feedback_becomes_regression_case(client):
    agent = create_agent(client)
    run = complete_run(client, agent['id'], 'Write outputs/script.py')
    tests = 'def test_output():\n    from script import main\n    assert callable(main)\n'
    response = client.post(f"/api/runs/{run['id']}/feedback", json={'rating': 'negative', 'correction': 'Export an importable main function.', 'test_code': tests})
    assert response.status_code == 201, response.text
    assert response.json()['status'] == 'eligible'
    cases = client.get(f"/api/agents/{agent['id']}/cases").json()
    assert len(cases) == 1 and cases[0]['test_code'] == tests
    assert cases[0]['source_run_id'] == run['id']


def test_http_requests_preserve_explicit_process_settings(client, monkeypatch):
    monkeypatch.setenv('BATCH_MAX_RUNS', '17')
    assert client.get('/health').status_code == 200
    import os
    assert os.environ['BATCH_MAX_RUNS'] == '17'


def test_artifact_preview_matches_download_and_respects_shared_scope(client):
    agent = create_agent(client)
    deployment = client.post(f"/api/agents/{agent['id']}/deploy", json={"slug": "preview-test"}).json()
    accepted = client.post('/api/deployments/preview-test/runs', json={"prompt": "Make a file", "token": deployment['token']}).json()
    run = wait_for(client, f"/api/runs/{accepted['id']}", {'completed'})
    artifact = next(a for a in run['artifacts'] if a['role'] == 'output')
    url = artifact['url']
    preview = client.get(url + '/preview', params={'token': deployment['token']})
    assert preview.status_code == 200
    assert preview.json()['content'] == client.get(url, params={'token': deployment['token']}).text
    assert preview.json()['truncated'] is False and 'path' not in preview.json()
    assert client.get(url + '/preview', params={'token': 'wrong'}).status_code == 403
    # A key from another deployment cannot read this snapshot.
    other = create_agent(client, 'Other agent')
    key = client.post(f"/api/agents/{other['id']}/deploy", json={"slug": "other-preview"}).json()['token']
    assert client.get(url + '/preview', params={'token': key}).status_code == 403


def test_artifact_preview_bounds_text_and_rejects_binary_and_linked_files(client, tmp_path):
    agent = create_agent(client)
    run = complete_run(client, agent['id'])
    artifact = next(a for a in run['artifacts'] if a['role'] == 'output')
    stored = client.app.state.platform.db.get('artifacts', artifact['id'])
    path = Path(stored['path'])
    url = artifact['url'] + '/preview'
    path.write_text('a' * (128 * 1024 - 1) + '€' + '\nrest')
    data = client.get(url).json()
    assert data['truncated'] is True and data['content'] == 'a' * (128 * 1024 - 1)
    path.write_bytes(b'\x00binary')
    assert client.get(url).status_code == 415
    path.write_bytes(b'\xffinvalid')
    assert client.get(url).status_code == 415
    outside = tmp_path / 'private.txt'
    outside.write_text('not an artifact')
    path.unlink()
    path.symlink_to(outside)
    assert client.get(url).status_code == 404


def test_external_worker_recovery_can_be_explicitly_disabled(tmp_path, monkeypatch):
    from backend.app.store import Store
    data = tmp_path / 'external-worker'
    db = Store(data)
    db.put('runs', {'id': 'run_external', 'status': 'running'})
    db.put('batches', {'id': 'batch_external', 'status': 'evaluating'})
    monkeypatch.setenv('RECOVER_JOBS_ON_START', '0')
    with TestClient(create_app(data)):
        assert db.get('runs', 'run_external')['status'] == 'running'
        assert db.get('batches', 'batch_external')['status'] == 'evaluating'
    monkeypatch.setenv('RECOVER_JOBS_ON_START', '1')
    with TestClient(create_app(data)):
        assert db.get('runs', 'run_external')['status'] == 'interrupted'
        assert db.get('batches', 'batch_external')['status'] == 'interrupted'
