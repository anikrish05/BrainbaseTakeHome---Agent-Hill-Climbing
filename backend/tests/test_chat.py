"""Conversation lifecycle tests use synthetic execution in temporary stores only."""
import asyncio
from pathlib import Path
import pytest
from backend.app import runtime
from backend.app.service import Platform, PlatformError
from backend.app.store import Store
from backend.tests.test_platform import drain
from backend.tests.test_api import client, create_agent, wait_for

@pytest.fixture
def chat_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, 'get_runtime_status', lambda: {'ready': True, 'provider': 'synthetic', 'model': 'test/model'})
    state = {'calls': []}
    async def execute(config, prompt, files, work_dir, emit, *, conversation=None):
        from backend.app.sandbox import DockerSandbox
        sandbox = DockerSandbox(work_dir)
        sandbox.prepare_inputs(files, reuse=conversation is not None)
        state['calls'].append({'config': config, 'prompt': prompt, 'conversation': conversation, 'workspace': work_dir})
        if state.get('entered'):
            state['entered'].set()
            await state['release'].wait()
        if prompt == 'follow up':
            assert (work_dir / 'input.txt').read_text() == 'original input'
            assert (work_dir / 'outputs' / 'script.py').read_text() == 'first script'
        output = work_dir / 'outputs' / 'script.py'
        output.write_text('revised script' if prompt == 'follow up' else 'first script')
        await emit('tool_end', {'name': 'write_file'})
        return {'output': 'Revised the existing script.' if prompt == 'follow up' else 'Created script.py.',
                'artifacts': [{'name': 'script.py', 'path': str(output)}], 'cost_usd': 0.001, 'tool_calls': 1}
    monkeypatch.setattr(runtime, 'execute_run', execute)
    p = Platform(Store(tmp_path / 'chat'))
    agent = p.create_agent({'name': 'Chat test', 'instructions': 'Synthetic only'})
    return p, agent, state


def test_context_files_and_immutable_artifact_snapshots_survive_turns_and_restart(chat_platform):
    p, a, state = chat_platform
    async def scenario():
        chat = p.create_session(a['id'])
        first = p.submit_session_run(chat['id'], 'first request', [{'name': 'input.txt', 'content': 'original input'}])
        await drain(p)
        original = p.db.all('artifacts', run_id=first['id'], role='output')[0]
        await p.close()
        # A new service instance uses SQLite history and the same workspace.
        resumed = Platform(Store(p.db.data_dir))
        try:
            resumed.recover()
            second = resumed.submit_session_run(chat['id'], 'follow up', [])
            await drain(resumed)
            view = resumed.session_view(chat['id'])
            assert view['turn_count'] == 2 and view['active_run_id'] is None
            assert state['calls'][0]['conversation'] == []
            history = state['calls'][1]['conversation']
            assert [m['role'] for m in history] == ['user', 'assistant']
            assert 'first request' in history[0]['content'] and 'input.txt' in history[0]['content']
            assert 'Created script.py.' in history[1]['content'] and 'outputs/script.py' in history[1]['content']
            assert state['calls'][0]['workspace'] == state['calls'][1]['workspace']
            assert Path(original['path']).read_text() == 'first script'
            revised = resumed.db.all('artifacts', run_id=second['id'], role='output')[0]
            assert Path(revised['path']).read_text() == 'revised script'
            assert 'conversation_context' not in resumed.run_view(second['id'])
            assert resumed.db.events(second['id'])[1]['type'] == 'conversation.context'
        finally:
            await resumed.close()
    asyncio.run(scenario())


def test_existing_chat_stays_pinned_new_chat_uses_new_version_and_clean_workspace(chat_platform):
    p, a, state = chat_platform
    async def scenario():
        try:
            chat = p.create_session(a['id'])
            first = p.submit_session_run(chat['id'], 'before change', [])
            await drain(p)
            new = p.create_version(a['id'], {'playbook': 'A newer rule'})
            second = p.submit_session_run(chat['id'], 'after change', [])
            await drain(p)
            fresh = p.create_session(a['id'])
            third = p.submit_session_run(fresh['id'], 'fresh', [])
            await drain(p)
            assert first['version_id'] == second['version_id'] == chat['version_id']
            assert third['version_id'] == new['id']
            assert state['calls'][2]['conversation'] == []
            assert state['calls'][2]['workspace'] != state['calls'][0]['workspace']
        finally:
            await p.close()
    asyncio.run(scenario())


def test_session_blocks_queued_and_running_overlapping_turns(chat_platform):
    p, a, state = chat_platform
    async def scenario():
        try:
            state.update(entered=asyncio.Event(), release=asyncio.Event())
            chat = p.create_session(a['id'])
            p.submit_session_run(chat['id'], 'first', [])
            with pytest.raises(PlatformError) as error:
                p.submit_session_run(chat['id'], 'queued overlap', [])
            assert error.value.status == 409
            await state['entered'].wait()
            with pytest.raises(PlatformError) as error:
                p.submit_session_run(chat['id'], 'running overlap', [])
            assert error.value.status == 409
            state['release'].set()
            await drain(p)
            assert len(p.session_runs(chat['id'])) == 1
        finally:
            state['release'].set()
            await p.close()
    asyncio.run(scenario())


def test_eval_and_one_shot_runs_remain_isolated_from_chat(chat_platform):
    p, a, state = chat_platform
    async def scenario():
        try:
            chat = p.create_session(a['id'])
            p.submit_session_run(chat['id'], 'chat', [])
            await drain(p)
            version = p.current_version(a['id'])
            ev = p.prepare_run(version, 'eval', [], source='eval')
            await p.execute(ev)
            p.submit_run(a['id'], 'one shot', [])
            await drain(p)
            assert [c['conversation'] for c in state['calls']] == [[], None, None]
            assert len({str(c['workspace']) for c in state['calls']}) == 3
            assert 'session_id' not in p.need('runs', ev['id'])
        finally:
            await p.close()
    asyncio.run(scenario())


def test_transcript_budget_keeps_complete_recent_message_pairs(chat_platform):
    p, a, _ = chat_platform
    try:
        runs = []
        for index in range(30):
            r = p.prepare_run(p.current_version(a['id']), f'turn {index} '+('x'*10000), [])
            runs.append(p.db.update('runs', r['id'], status='completed', output='y'*10000))
        context = p.conversation_context(runs)
        assert len(context) % 2 == 0 and len(context) <= 24
        assert sum(len(m['content']) for m in context) <= 24000
        assert 'turn 29' in context[-2]['content']
        assert 'turn 0 ' not in str(context)
        assert all(m['role'] == ('user' if i % 2 == 0 else 'assistant') for i, m in enumerate(context))
    finally:
        asyncio.run(p.close())


def test_interrupted_turn_does_not_permanently_lock_conversation(chat_platform):
    p, a, _ = chat_platform
    async def scenario():
        try:
            chat = p.create_session(a['id'])
            version = p.current_version(a['id'])
            run = p.prepare_run(version, 'interrupted prior request', [])
            p.db.update('runs', run['id'], session_id=chat['id'], turn_index=1)
            p.recover()
            next_run = p.submit_session_run(chat['id'], 'resume', [])
            await drain(p)
            assert p.need('runs', next_run['id'])['status'] == 'completed'
        finally:
            await p.close()
    asyncio.run(scenario())


def test_shared_conversation_routes_enforce_deployment_scope_and_version_pin(client):
    a = create_agent(client, 'Shared chat')
    b = create_agent(client, 'Other agent')
    dep = client.post(f"/api/agents/{a['id']}/deploy", json={'slug': 'shared-chat'}).json()
    other = client.post(f"/api/agents/{b['id']}/deploy", json={'slug': 'other-chat'}).json()
    assert client.post('/api/deployments/shared-chat/sessions', json={}).status_code == 403
    key = {'X-Deployment-Key': dep['token']}
    response = client.post('/api/deployments/shared-chat/sessions', json={}, headers=key)
    assert response.status_code == 201
    chat = response.json(); url = '/api/deployments/shared-chat/sessions/'+chat['id']
    assert client.get(url).status_code == 403
    assert client.get(url, headers=key).status_code == 200
    assert client.get('/api/deployments/other-chat/sessions/'+chat['id'], headers={'X-Deployment-Key': other['token']}).status_code == 404
    assert client.get(f"/api/agents/{b['id']}/sessions/{chat['id']}").status_code == 404
    newer = client.post(f"/api/agents/{a['id']}/versions", json={'playbook': 'New rule'}).json()
    client.post(f"/api/agents/{a['id']}/deploy", json={'version_id': newer['id']})
    accepted = client.post(url+'/runs', json={'prompt': 'test'}, headers=key)
    assert accepted.status_code == 202
    run = wait_for(client, '/api/runs/'+accepted.json()['id'], {'completed','failed'})
    assert run['status'] == 'completed' and run['version_id'] == chat['version_id']
    assert run['source'] == 'deployment' and run['session_id'] == chat['id']
    fresh = client.post('/api/deployments/shared-chat/sessions', json={}, headers=key).json()
    assert fresh['version_id'] == newer['id']
    note = client.post('/api/runs/'+run['id']+'/feedback', json={'rating':'positive','correction':'Good follow-up.'})
    assert note.status_code == 201 and note.json()['status'] == 'needs_context'
    check = client.post('/api/runs/'+run['id']+'/feedback', json={'rating':'negative','correction':'Change this', 'test_code':'def test_a():\n    assert True'})
    assert check.status_code == 422
    assert client.get(f"/api/agents/{a['id']}/cases").json() == []
    rotated = client.post(f"/api/agents/{a['id']}/deploy", json={'rotate_key':True}).json()
    assert client.get(url, headers=key).status_code == 403
    assert client.get(url, headers={'X-Deployment-Key':rotated['token']}).status_code == 200
