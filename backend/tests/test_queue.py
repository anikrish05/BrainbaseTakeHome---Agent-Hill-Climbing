"""Cancellation must leave durable terminal status, including before a slot."""

import asyncio

from backend.app import runtime
from backend.app.service import Platform
from backend.app.store import Store


def make_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "get_runtime_status", lambda: {
        "ready": True, "provider": "openai", "model": "synthetic-test-model",
    })
    platform = Platform(Store(tmp_path.resolve() / "data"))
    agent = platform.create_agent({"name": "Cancellation test", "instructions": "Synthetic test configuration."})
    version = platform.need("versions", agent["current_version_id"])
    return platform, version


def test_cancelling_run_waiting_for_worker_slot_persists_interrupted(tmp_path, monkeypatch):
    platform, version = make_platform(tmp_path, monkeypatch)

    async def scenario():
        # Evaluation traffic has its own worker pool; occupy every slot in it.
        held = 0
        while not platform.eval_semaphore.locked():
            await platform.eval_semaphore.acquire()
            held += 1
        run = platform.prepare_run(version, "Must never execute.", [], source="eval")
        task = platform.spawn(platform.execute(run))
        await asyncio.sleep(0)
        assert platform.need("runs", run["id"])["status"] == "queued"
        task.cancel()
        try:
            await asyncio.gather(task, return_exceptions=True)
            record = platform.need("runs", run["id"])
            assert task.cancelled()
            assert record["status"] == "interrupted"
            assert record["finished_at"] is not None
            assert [event["type"] for event in platform.db.events(run["id"])].count("run.error") == 1
        finally:
            for _ in range(held):
                platform.eval_semaphore.release()
            await platform.close()

    asyncio.run(scenario())


def test_cancelling_active_run_does_not_duplicate_terminal_error(tmp_path, monkeypatch):
    platform, version = make_platform(tmp_path, monkeypatch)

    async def scenario():
        entered = asyncio.Event()

        async def blocked_runtime(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(runtime, "execute_run", blocked_runtime)
        run = platform.prepare_run(version, "A deliberately blocked synthetic run.", [], source="eval")
        task = platform.spawn(platform.execute(run))
        await entered.wait()
        assert platform.need("runs", run["id"])["status"] == "running"
        task.cancel()
        try:
            await asyncio.gather(task, return_exceptions=True)
            record = platform.need("runs", run["id"])
            assert record["status"] == "interrupted"
            assert record["finished_at"] is not None
            assert [event["type"] for event in platform.db.events(run["id"])].count("run.error") == 1
        finally:
            await platform.close()

    asyncio.run(scenario())
