"""Synthetic model responses test orchestration, never masquerade as app runs."""

import asyncio
import json
import os

from agents import Model
from agents.items import ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
import pytest

from backend.app import runtime


class SyntheticClient:
    closed = False

    async def close(self):
        self.closed = True


def message(text):
    return ResponseOutputMessage(
        type="message", id="synthetic-message", role="assistant", status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class SyntheticModel(Model):
    def __init__(self, outputs, raw_usage=None):
        self.outputs = iter(outputs)
        self.raw_usage = raw_usage
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        self.calls += 1
        return ModelResponse(
            output=next(self.outputs), usage=Usage(requests=1, input_tokens=100, output_tokens=50, total_tokens=150),
            response_id=f"synthetic-{self.calls}", raw_usage=self.raw_usage,
        )

    async def stream_response(self, *args, **kwargs):
        yield None


def test_proposal_uses_actual_sdk_structured_output_and_emits_usage(monkeypatch):
    expected = {"playbook": "Keep the most recently updated customer record.", "rationale": "Source run synthetic-123 clarified the duplicate policy."}
    model = SyntheticModel([[message(json.dumps({"candidates": [expected]}))]], {"cost": 0.001})
    client = SyntheticClient()
    monkeypatch.setattr(runtime, "build_model", lambda config: (model, client, "synthetic", "synthetic-model"))
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    result = asyncio.run(runtime.propose_playbook({"instructions": "Transform CSV files."}, [{"source_run_id": "synthetic-123", "correction": "Retain newest updates."}], emit))
    assert result["playbook"] == expected["playbook"]
    assert result["rationale"] == expected["rationale"]
    assert [{k: c[k] for k in expected} for c in result["candidates"]] == [expected]
    assert result["candidates"][0]["model"] is None and result["candidates"][0]["max_turns"] is None
    assert result["tokens_in"] == 100 and result["tokens_out"] == 50
    assert result["cost_usd"] == 0.001 and result["cost_kind"] == "actual"
    assert [kind for kind, payload in events] == ["model_start", "model_end"]
    assert events[0][1]["call_id"] == events[1][1]["call_id"]
    assert client.closed


def test_model_event_redacts_provider_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-private-api-key")
    model = SyntheticModel([[message("Do not expose synthetic-private-api-key")]])
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    observed = runtime.ObservedModel(model, "synthetic-model", "synthetic", emit)
    asyncio.run(observed.get_response())
    assert "synthetic-private-api-key" not in json.dumps(events)
    assert "[REDACTED]" in json.dumps(events)
    assert observed.usage()["cost_usd"] is None
    assert observed.usage()["cost_kind"] == "unknown"


def test_failed_model_calls_close_trace_pairs():
    class FailingModel(SyntheticModel):
        async def get_response(self, *args, **kwargs):
            raise RuntimeError("Synthetic provider outage")

    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    observed = runtime.ObservedModel(FailingModel([]), "synthetic-model", "synthetic", emit)
    with pytest.raises(RuntimeError, match="outage"):
        asyncio.run(observed.get_response())
    assert [kind for kind, payload in events] == ["model_start", "model_end"]
    assert events[-1][1]["status"] == "error"


@pytest.mark.skipif(os.getenv("RUN_DOCKER_TESTS") != "1", reason="Opt-in integration: RUN_DOCKER_TESTS=1 requires a Docker engine and python image")
def test_actual_sdk_tool_orchestration_and_real_docker_without_api_spend(monkeypatch, tmp_path):
    operations = [
        ("write_file", {"path": "transform.py", "content": "import csv\nrows=list(csv.DictReader(open('input.csv')))\nwith open('outputs/result.csv','w') as f:\n w=csv.DictWriter(f,fieldnames=['id','value']); w.writeheader(); w.writerows(rows)\n"}),
        ("bash", {"command": "python transform.py", "timeout_s": 20}),
        ("read_file", {"path": "outputs/result.csv"}),
    ]
    outputs = [[ResponseFunctionToolCall(type="function_call", call_id=f"synthetic-tool-{i}", name=name, arguments=json.dumps(arguments))] for i, (name, arguments) in enumerate(operations)]
    outputs.append([message("Created result.csv.")])
    model = SyntheticModel(outputs)
    client = SyntheticClient()
    monkeypatch.setattr(runtime, "build_model", lambda config: (model, client, "synthetic", "synthetic-model"))
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    result = asyncio.run(runtime.execute_run(
        {"instructions": "Copy CSV rows.", "max_turns": 8}, "Transform input.csv.",
        [{"name": "input.csv", "content": "id,value\n1,10\n"}], tmp_path.resolve() / "workspace", emit,
    ))
    assert result["output"] == "Created result.csv."
    assert result["tool_calls"] == 3
    assert result["tokens_in"] == 400 and result["tokens_out"] == 200
    assert result["cost_kind"] == "unknown"
    assert result["artifacts"][0]["name"] == "result.csv"
    starts = [payload["call_id"] for kind, payload in events if kind == "tool_start"]
    ends = [payload["call_id"] for kind, payload in events if kind == "tool_end"]
    assert starts == ends == ["synthetic-tool-0", "synthetic-tool-1", "synthetic-tool-2"]
    assert len([kind for kind, payload in events if kind == "model_end"]) == 4
    assert client.closed


def test_judge_rejects_duplicated_or_reordered_criteria(monkeypatch):
    async def structured(*args, **kwargs):
        output = runtime.JudgeVerdict(verdicts=[runtime.CriterionVerdict(criterion='a', met=True, reason='yes'), runtime.CriterionVerdict(criterion='a', met=True, reason='yes')])
        return output, {'cost_usd': 0.01}, 'test/model'
    monkeypatch.setattr(runtime, '_structured_call', structured)
    async def emit(*args):
        pass
    with pytest.raises(runtime.RuntimeExecutionError, match='exactly once'):
        asyncio.run(runtime.judge_output({}, ['a', 'b'], 'task', 'done', [], emit))


def test_improver_receives_rejected_attempts_and_actual_reward_weights(monkeypatch):
    seen = {}
    async def structured(config, model_env, name, instructions, output_type, payload, emit, **kwargs):
        seen.update(payload)
        return runtime.PlaybookProposals(candidates=[runtime.PlaybookProposal(playbook='Verify once, then finish.', rationale='Use measured failures.')]), {'cost_usd': 0.01}, 'test/model'
    async def emit(*args):
        pass
    monkeypatch.setattr(runtime, '_structured_call', structured)
    attempts = [{'status': 'no_gain', 'candidates': [{'model': 'slow/model', 'reason': 'regressed a passing case'}]}]
    weights = {'cost_weight': 3, 'latency_weight': 0.1}
    asyncio.run(runtime.propose_playbook({}, [], emit, context={'prior_attempts': attempts, 'reward_config': weights}))
    assert seen['prior_attempts'] == attempts
    assert seen['reward_config'] == weights


def test_runtime_accepts_portable_message_history_and_reuses_workspace(tmp_path, monkeypatch):
    from backend.app.sandbox import DockerSandbox
    class CapturingModel(SyntheticModel):
        inputs = []
        async def get_response(self, *args, **kwargs):
            self.inputs.append(kwargs.get('input', args[1] if len(args)>1 else None))
            return await super().get_response(*args, **kwargs)
    model = CapturingModel([[message('First reply')], [message('Follow-up reply')]])
    monkeypatch.setattr(runtime, 'build_model', lambda config: (model, SyntheticClient(), 'synthetic', 'test/model'))
    async def start(self):
        self.started = True
    async def stop(self):
        self.started = False
    monkeypatch.setattr(DockerSandbox, 'start', start)
    monkeypatch.setattr(DockerSandbox, 'stop', stop)
    async def emit(*args):
        pass
    async def scenario():
        work = tmp_path.resolve() / 'workspace'
        await runtime.execute_run({}, 'Remember project Cedar.', [{'name':'input.txt','content':'42'}], work, emit, conversation=[])
        (work / 'outputs' / 'script.py').write_text('fixture code')
        history = [{'role':'user','content':'Remember project Cedar.'}, {'role':'assistant','content':'First reply'}]
        result = await runtime.execute_run({}, 'What project? Update the script.', [], work, emit, conversation=history)
        assert (work / 'input.txt').read_text() == '42'
        assert result['artifacts'][0]['name'] == 'script.py'
        serialized = json.dumps(model.inputs[1])
        assert 'Remember project Cedar.' in serialized and 'First reply' in serialized and 'What project?' in serialized
        assert 'function_call_output' not in serialized
    asyncio.run(scenario())
