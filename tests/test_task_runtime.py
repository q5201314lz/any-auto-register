from __future__ import annotations

import threading
import time

import services.task_runtime as task_runtime_module
from services.task_runtime import TaskRuntime


def _stop_runtime(runtime: TaskRuntime) -> None:
    runtime.stop()
    if runtime._dispatcher:
        runtime._dispatcher.join(timeout=1)


def test_same_platform_tasks_start_in_parallel(monkeypatch):
    pending = [
        {"id": "task-1", "platform": "chatgpt", "account_keys": []},
        {"id": "task-2", "platform": "chatgpt", "account_keys": []},
    ]
    started: list[str] = []
    both_started = threading.Event()
    release_workers = threading.Event()

    def claim(**kwargs):
        if not pending:
            return None
        candidate = pending[0]
        platform = candidate["platform"]
        if kwargs["running_platform_counts"].get(platform, 0) >= kwargs["max_parallel_per_platform"]:
            return None
        return pending.pop(0)

    def execute(task_id: str):
        started.append(task_id)
        if len(started) == 2:
            both_started.set()
        release_workers.wait(timeout=2)

    monkeypatch.setattr(task_runtime_module, "mark_incomplete_tasks_interrupted", lambda: None)
    monkeypatch.setattr(task_runtime_module, "reconcile_local_mailbox_reservations", lambda: [])
    monkeypatch.setattr(task_runtime_module, "claim_next_runnable_task", claim)
    monkeypatch.setattr(task_runtime_module, "execute_task", execute)

    runtime = TaskRuntime(max_parallel_tasks=2, max_parallel_per_platform=2, poll_interval=5)
    runtime.start()
    try:
        assert both_started.wait(timeout=1)
        assert set(started) == {"task-1", "task-2"}
    finally:
        release_workers.set()
        _stop_runtime(runtime)


def test_wake_up_dispatches_new_task_immediately(monkeypatch):
    claim_attempted = threading.Event()
    task_ready = threading.Event()
    task_started = threading.Event()
    claimed = False

    def claim(**_kwargs):
        nonlocal claimed
        claim_attempted.set()
        if task_ready.is_set() and not claimed:
            claimed = True
            return {"id": "task-1", "platform": "chatgpt", "account_keys": []}
        return None

    monkeypatch.setattr(task_runtime_module, "mark_incomplete_tasks_interrupted", lambda: None)
    monkeypatch.setattr(task_runtime_module, "reconcile_local_mailbox_reservations", lambda: [])
    monkeypatch.setattr(task_runtime_module, "claim_next_runnable_task", claim)
    monkeypatch.setattr(task_runtime_module, "execute_task", lambda _task_id: task_started.set())

    runtime = TaskRuntime(poll_interval=5)
    runtime.start()
    try:
        assert claim_attempted.wait(timeout=1)
        task_ready.set()
        started_at = time.monotonic()
        runtime.wake_up()
        assert task_started.wait(timeout=0.5)
        assert time.monotonic() - started_at < 0.5
    finally:
        _stop_runtime(runtime)


def test_tasks_for_same_account_remain_serialized(monkeypatch):
    pending = [
        {"id": "task-1", "platform": "chatgpt", "account_keys": ["account:7"]},
        {"id": "task-2", "platform": "chatgpt", "account_keys": ["account:7"]},
    ]
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def claim(**kwargs):
        for index, candidate in enumerate(pending):
            if kwargs["busy_account_keys"].intersection(candidate["account_keys"]):
                continue
            return pending.pop(index)
        return None

    def execute(task_id: str):
        if task_id == "task-1":
            first_started.set()
            release_first.wait(timeout=2)
        else:
            second_started.set()

    monkeypatch.setattr(task_runtime_module, "mark_incomplete_tasks_interrupted", lambda: None)
    monkeypatch.setattr(task_runtime_module, "reconcile_local_mailbox_reservations", lambda: [])
    monkeypatch.setattr(task_runtime_module, "claim_next_runnable_task", claim)
    monkeypatch.setattr(task_runtime_module, "execute_task", execute)

    runtime = TaskRuntime(max_parallel_tasks=2, max_parallel_per_platform=2, poll_interval=5)
    runtime.start()
    try:
        assert first_started.wait(timeout=1)
        assert not second_started.wait(timeout=0.1)
        release_first.set()
        assert second_started.wait(timeout=1)
    finally:
        release_first.set()
        _stop_runtime(runtime)
