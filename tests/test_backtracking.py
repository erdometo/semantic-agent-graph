import pytest
from unittest.mock import MagicMock
import datetime

from semantic_agent_graph import Event, Run, SQLiteEventStore, ReactiveRuntime
from semantic_agent_graph.backtracking import PredictiveBacktrackingEngine


def test_backtracking_no_projection():
    store = SQLiteEventStore(":memory:")
    runtime = ReactiveRuntime(store=store)
    engine = PredictiveBacktrackingEngine(runtime)

    assert engine.check_dead_end("run-1") is None


def test_backtracking_check_dead_end_predicted():
    store = SQLiteEventStore(":memory:")
    mock_projection = MagicMock()
    runtime = ReactiveRuntime(store=store, projection=mock_projection)
    engine = PredictiveBacktrackingEngine(runtime)

    # Create run
    run = Run(
        run_id="run-active",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        goal="Connect to database"
    )
    store.create_run(run)

    # Seed events
    with runtime.active_run("run-active"):
        ev0 = runtime.emit("action.executed", {"action": "initialize", "status": "success"})
        ev1 = runtime.emit("object.created", {"id": "pg", "name": "Postgres", "type": "Entity"})
        ev2 = runtime.emit("object.created", {"id": "timeout", "name": "TimeoutError", "type": "Error"})

    # Mock Neo4j session and query results
    # Query should return two historical runs that failed
    mock_session = MagicMock()
    mock_projection.driver.session.return_value.__enter__.return_value = mock_session
    
    mock_records = [
        {"hist_run_id": "run-hist-1", "outcome": "run.failed", "matched_names": ["Postgres", "TimeoutError"]},
        {"hist_run_id": "run-hist-2", "outcome": "run.failed", "matched_names": ["Postgres", "TimeoutError"]},
    ]
    mock_session.run.return_value = mock_records

    # Check dead end
    result = engine.check_dead_end("run-active", threshold_entities=2, threshold_failure_rate=0.75)

    assert result is not None
    assert result["predicted_dead_end"] is True
    assert result["failure_rate"] == 1.0
    assert result["failed_runs"] == ["run-hist-1", "run-hist-2"]
    assert set(result["matched_entities"]) == {"Postgres", "TimeoutError"}
    # The first matched event should be ev1 because it matched "Postgres"
    assert result["first_matched_event_id"] == ev1.id


def test_backtrack_fork_and_feedback():
    store = SQLiteEventStore(":memory:")
    mock_projection = MagicMock()
    runtime = ReactiveRuntime(store=store, projection=mock_projection)
    engine = PredictiveBacktrackingEngine(runtime)

    # Create run
    run = Run(
        run_id="run-active",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        goal="Connect to database"
    )
    store.create_run(run)

    # Seed events
    with runtime.active_run("run-active"):
        ev0 = runtime.emit("action.executed", {"action": "initialize", "status": "success"})
        ev1 = runtime.emit("object.created", {"id": "pg", "name": "Postgres", "type": "Entity"})
        ev2 = runtime.emit("object.created", {"id": "timeout", "name": "TimeoutError", "type": "Error"})

    dead_end_info = {
        "predicted_dead_end": True,
        "failure_rate": 1.0,
        "failed_runs": ["run-hist-1"],
        "matched_entities": ["Postgres", "TimeoutError"],
        "first_matched_event_id": ev1.id,
        "first_matched_seq": ev1.seq
    }

    # Execute backtrack
    new_run_id = "run-active-forked"
    engine.backtrack("run-active", new_run_id, dead_end_info)

    # Verify run is forked in SQLite store
    forked_run = store.get_run(new_run_id)
    assert forked_run is not None
    assert forked_run.parent_run_id == "run-active"
    assert forked_run.forked_at_event_id == ev0.id # should fork at ev0, which is the stable event before ev1

    # Verify events copied to the new run
    forked_events = store.get_events(new_run_id)
    # Copied events + run.backtracked feedback event
    assert len(forked_events) == 2
    assert forked_events[0].id == ev0.id
    assert forked_events[0].run_id == new_run_id

    # Verify feedback event
    feedback = forked_events[1]
    assert feedback.type == "run.backtracked"
    assert "Do not repeat actions that touch" in feedback.payload["message"]
    assert "Postgres" in feedback.payload["message"]
    assert "TimeoutError" in feedback.payload["message"]
    assert feedback.payload["failed_historical_runs"] == ["run-hist-1"]
    assert feedback.payload["matched_entities"] == ["Postgres", "TimeoutError"]
