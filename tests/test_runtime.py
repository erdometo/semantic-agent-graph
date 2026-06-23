import pytest
import datetime
from unittest.mock import MagicMock

from semantic_agent_graph import Event, Run, SQLiteEventStore, ReactiveRuntime, behavior, Neo4jProjection


def test_runtime_initialization():
    store = SQLiteEventStore(":memory:")
    runtime = ReactiveRuntime(store=store)
    assert runtime.store == store
    assert runtime.projection is None
    assert isinstance(runtime.behaviors, list)
    assert isinstance(runtime.event_queue, list)


def test_behavior_registration():
    store = SQLiteEventStore(":memory:")
    runtime = ReactiveRuntime(store=store)

    # 1. Test instance-level behavior registration
    @runtime.behavior(on_events=["test.event"], where={"status": "ok"})
    def my_instance_behavior(event):
        pass

    assert len(runtime.behaviors) == 1
    assert runtime.behaviors[0]["name"] == "my_instance_behavior"
    assert runtime.behaviors[0]["on_events"] == ["test.event"]
    assert runtime.behaviors[0]["where"] == {"status": "ok"}
    assert runtime.behaviors[0]["fn"] == my_instance_behavior

    # 2. Test manual registration
    def my_manual_behavior(event):
        pass

    runtime.register_behavior("my_manual", ["another.event"], my_manual_behavior, {"val": 42})
    assert len(runtime.behaviors) == 2
    assert runtime.behaviors[1]["name"] == "my_manual"
    assert runtime.behaviors[1]["on_events"] == ["another.event"]
    assert runtime.behaviors[1]["where"] == {"val": 42}


def test_global_behavior_decorator():
    # Define a global behavior before instantiating the runtime
    @behavior(on_events=["global.event"], where={"global_key": "yes"})
    def my_global_behavior(event):
        pass

    store = SQLiteEventStore(":memory:")
    runtime = ReactiveRuntime(store=store)

    # The runtime should automatically register the global behavior
    global_matched = [b for b in runtime.behaviors if b["name"] == "my_global_behavior"]
    assert len(global_matched) == 1
    assert global_matched[0]["on_events"] == ["global.event"]
    assert global_matched[0]["where"] == {"global_key": "yes"}


def test_emit_and_context():
    store = SQLiteEventStore(":memory:")
    runtime = ReactiveRuntime(store=store)

    # Create run first in SQLite
    run = Run(
        run_id="run-123",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        goal="test emit"
    )
    store.create_run(run)

    # Emitting without active context should raise RuntimeError
    with pytest.raises(RuntimeError):
        runtime.emit("test.event", {"data": 1})

    # Emit with context manager
    with runtime.active_run("run-123"):
        assert runtime.active_run_id == "run-123"
        event = runtime.emit("test.event", {"status": "success"}, actor="agent", frame_id="frame-A")

    assert event.id is not None
    assert event.type == "test.event"
    assert event.payload == {"status": "success"}
    assert event.actor == "agent"
    assert event.frame_id == "frame-A"
    assert event.run_id == "run-123"
    assert event.timestamp is not None
    assert event.seq is not None

    # Check store contains the event
    events = store.get_events("run-123")
    assert len(events) == 1
    assert events[0].id == event.id


def test_dispatch_loop():
    store = SQLiteEventStore(":memory:")
    runtime = ReactiveRuntime(store=store)

    run = Run(
        run_id="run-1",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        goal="test dispatch"
    )
    store.create_run(run)

    triggered_payloads = []

    # Behavior that responds to "step.1" and emits "step.2"
    @runtime.behavior(on_events=["step.1"])
    def on_step_1(rt, event):
        triggered_payloads.append(event.payload)
        rt.emit("step.2", {"value": event.payload["value"] + 1})

    # Behavior that responds to "step.2" and does not emit
    @runtime.behavior(on_events=["step.2"])
    def on_step_2(event):
        triggered_payloads.append(event.payload)

    with runtime.active_run("run-1"):
        # Emit step.1 event
        runtime.emit("step.1", {"value": 10})
        
        # Run dispatch loop
        runtime.dispatch_loop()

    # Both behaviors should have been triggered sequentially
    assert len(triggered_payloads) == 2
    assert triggered_payloads[0] == {"value": 10}
    assert triggered_payloads[1] == {"value": 11}

    # Verify event store has both events
    events = store.get_events("run-1")
    assert len(events) == 2 # step.1 and step.2 (emitted by step.1)
    # Events list:
    # 1. step.1 (emitted initially)
    # 2. step.2 (emitted inside on_step_1)
    assert events[0].type == "step.1"
    assert events[1].type == "step.2"


def test_llm_caching():
    store = SQLiteEventStore(":memory:")
    runtime = ReactiveRuntime(store=store)

    run = Run(
        run_id="run-c",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        goal="test LLM caching"
    )
    store.create_run(run)

    call_count = 0
    def mock_llm_call():
        nonlocal call_count
        call_count += 1
        return f"LLM Response {call_count}"

    with runtime.active_run("run-c"):
        # 1. First call (cache miss)
        res1 = runtime.llm_call(
            system_prompt="sys",
            user_prompt="user",
            model="gpt-4",
            settings={"temp": 0.7},
            call_fn=mock_llm_call
        )
        assert res1 == "LLM Response 1"
        assert call_count == 1

        # 2. Second call with same parameters (cache hit)
        res2 = runtime.llm_call(
            system_prompt="sys",
            user_prompt="user",
            model="gpt-4",
            settings={"temp": 0.7},
            call_fn=mock_llm_call
        )
        assert res2 == "LLM Response 1"
        # call_fn should not have been called again
        assert call_count == 1

        # 3. Third call with different settings (cache miss)
        res3 = runtime.llm_call(
            system_prompt="sys",
            user_prompt="user",
            model="gpt-4",
            settings={"temp": 0.8}, # different settings
            call_fn=mock_llm_call
        )
        assert res3 == "LLM Response 2"
        assert call_count == 2

    # Check store events
    events = store.get_events("run-c")
    # Expected sequence of events:
    # 1. llm.requested (call 1)
    # 2. llm.responded (call 1, cached=False)
    # 3. llm.requested (call 2)
    # 4. llm.responded (call 2, cached=True)
    # 5. llm.requested (call 3)
    # 6. llm.responded (call 3, cached=False)
    assert len(events) == 6
    assert events[1].type == "llm.responded"
    assert events[1].payload["cached"] is False
    assert events[3].type == "llm.responded"
    assert events[3].payload["cached"] is True
    assert events[3].payload["response"] == "LLM Response 1"
    assert events[5].type == "llm.responded"
    assert events[5].payload["cached"] is False


def test_fork_run_and_projection():
    store = SQLiteEventStore(":memory:")
    mock_projection = MagicMock(spec=Neo4jProjection)
    runtime = ReactiveRuntime(store=store, projection=mock_projection)

    # Create parent run
    parent = Run(
        run_id="parent-run",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        goal="parent goal",
        frame_id="root-frame"
    )
    store.create_run(parent)

    # Emit some events in parent run
    with runtime.active_run("parent-run"):
        ev1 = runtime.emit("event.1", {"v": 1})
        ev2 = runtime.emit("event.2", {"v": 2})

    # Fork run at ev1
    forked_run = runtime.fork_run(
        parent_run_id="parent-run",
        new_run_id="fork-run",
        forked_at_event_id=ev1.id
    )

    assert forked_run.run_id == "fork-run"
    assert forked_run.parent_run_id == "parent-run"
    assert forked_run.forked_at_event_id == ev1.id

    # Check database copied events
    parent_events = store.get_events("parent-run")
    forked_events = store.get_events("fork-run")
    
    assert len(parent_events) == 2
    assert len(forked_events) == 1
    assert forked_events[0].id == ev1.id
    assert forked_events[0].run_id == "fork-run"

    # Verify projection fork_run was called
    mock_projection.fork_run.assert_called_once()
    args, kwargs = mock_projection.fork_run.call_args
    assert kwargs.get("parent_run_id") == "parent-run"
    assert kwargs.get("new_run").run_id == "fork-run"
    assert kwargs.get("forked_at_event_id") == ev1.id
    assert len(kwargs.get("copied_events")) == 1
    assert kwargs.get("copied_events")[0].id == ev1.id
