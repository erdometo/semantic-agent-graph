import uuid
import datetime
import time
from fastapi.testclient import TestClient
from semantic_agent_graph.api import app, lifecycle
from semantic_agent_graph.models import Run, Event

def test_websocket_connection_and_broadcast():
    """
    Verifies that the WebSocket endpoint accepts connections and correctly
    broadcasts run creation and event append messages when database operations occur.
    """
    # Using context manager for TestClient ensures lifespans are run,
    # which starts/stops the background broadcast worker.
    with TestClient(app) as client:
        with client.websocket_connect("/api/ws") as websocket:
            # 1. Simulate new run creation
            run_id = f"run_ws_test_{uuid.uuid4().hex[:8]}"
            run = Run(
                run_id=run_id,
                goal="Verify WebSocket streaming functionality",
                created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            )
            lifecycle.store.create_run(run)
            
            # 2. Simulate appending a new event to that run
            event = Event(
                id=str(uuid.uuid4()),
                type="test.websocket_ping",
                actor="test_runner",
                payload={"ping": "pong"},
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                run_id=run_id
            )
            lifecycle.store.append_event(event)
            
            # 3. Allow time for background worker to flush queue (1s delay configured on worker)
            time.sleep(1.2)
            
            # 4. Fetch the WebSocket broadcast payloads
            run_msg = websocket.receive_json()
            assert run_msg["type"] == "runs.created"
            assert len(run_msg["runs"]) == 1
            assert run_msg["runs"][0]["run_id"] == run_id
            assert run_msg["runs"][0]["goal"] == run.goal
            
            event_msg = websocket.receive_json()
            assert event_msg["type"] == "events.appended"
            assert len(event_msg["events"]) == 1
            assert event_msg["events"][0]["run_id"] == run_id
            assert event_msg["events"][0]["type"] == "test.websocket_ping"
            assert event_msg["events"][0]["payload"] == {"ping": "pong"}
