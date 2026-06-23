import datetime
import logging
import json
from typing import List, Dict, Any

from semantic_agent_graph import (
    Event,
    Run,
    SQLiteEventStore,
    ReactiveRuntime,
    Neo4jProjection,
    EntityExtractor,
    Neo4jMemoryTool,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class MockNeo4jMemoryTool:
    """
    Mock memory retrieval tool for offline verification when Neo4j is not running.
    """

    def __init__(self, uri=None, auth=None):
        self.uri = uri
        self.auth = auth
        self.driver = None

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def query_past_trajectories(self, entity_names: List[str]) -> Dict[str, Any]:
        print(f"[MockNeo4jMemoryTool] query_past_trajectories called with: {entity_names}")
        # Return a simulated result representing the successful sequence in Run 1
        return {
            "nodes": [
                {
                    "id": "TimeoutError",
                    "labels": ["Entity", "Error"],
                    "properties": {"id": "TimeoutError", "name": "TimeoutError", "type": "Error"}
                },
                {
                    "id": "event_timeout",
                    "labels": ["Event"],
                    "properties": {
                        "id": "event_timeout",
                        "type": "object.created",
                        "payload": {"name": "TimeoutError", "type": "Error"}
                    }
                },
                {
                    "id": "event_flush",
                    "labels": ["Event"],
                    "properties": {
                        "id": "event_flush",
                        "type": "action.executed",
                        "payload": {"action": "Flush DNS cache", "status": "success"}
                    }
                },
                {
                    "id": "event_completed",
                    "labels": ["Event"],
                    "properties": {
                        "id": "event_completed",
                        "type": "run.completed",
                        "payload": {"status": "success"}
                    }
                }
            ],
            "relationships": [
                {
                    "type": "NEXT",
                    "start": "event_timeout",
                    "end": "event_flush",
                    "properties": {}
                },
                {
                    "type": "NEXT",
                    "start": "event_flush",
                    "end": "event_completed",
                    "properties": {}
                },
                {
                    "type": "PROCESSED",
                    "start": "event_timeout",
                    "end": "TimeoutError",
                    "properties": {}
                }
            ]
        }


def main():
    print("=== Semantic-Agent-Graph Integration & Verification Demo ===")

    # Initialize SQLite event store in-memory for the demo
    store = SQLiteEventStore(":memory:")
    print("SQLite Event Store initialized (in-memory).")

    # Connect to Neo4j and check availability
    neo4j_uri = "bolt://localhost:7687"
    neo4j_auth = ("neo4j", "password")
    neo4j_available = True
    projection = None

    try:
        # Attempt to initialize projection and clear database
        projection = Neo4jProjection(uri=neo4j_uri, auth=neo4j_auth)
        # Verify connection by running a simple query
        with projection.driver.session() as session:
            session.run("RETURN 1")
        print("Connected to Neo4j successfully. Clearing database...")
        projection.clear_db()
        memory_tool = Neo4jMemoryTool(uri=neo4j_uri, auth=neo4j_auth)
    except Exception as e:
        print(f"\nWARNING: Local Neo4j is not available on {neo4j_uri} ({e}).")
        print("Running demo with mocked/simulated Neo4j trajectory layer fallback.\n")
        neo4j_available = False
        projection = None
        memory_tool = MockNeo4jMemoryTool()

    # Initialize reactive runtime and extractor
    runtime = ReactiveRuntime(store=store, projection=projection)
    extractor = EntityExtractor()

    # =========================================================================
    # Scenario Run 1 (Episode Creation & Blooming)
    # =========================================================================
    print("\n=== Scenario Run 1: Episode Creation & Blooming ===")
    run_1_id = "run_1"
    run_1 = Run(
        run_id=run_1_id,
        goal="Connect to pg database",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    store.create_run(run_1)

    with runtime.active_run(run_1_id):
        print(f"Starting run: '{run_1_id}' with goal: '{run_1.goal}'")

        log_text = "Connection timeout trying to connect to pg database on port 5432."
        print(f"Simulating connection log: '{log_text}'")

        # Extract entities and project to Neo4j
        entities, relations = extractor.extract(log_text)
        print("Extracted entities:")
        for entity in entities:
            print(f"  - Entity: id={entity.id}, name={entity.name}, type={entity.type}")
            runtime.emit("object.created", {
                "id": entity.id,
                "name": entity.name,
                "type": entity.type,
                "data": entity.data
            })

        print("Extracted relations:")
        for relation in relations:
            print(f"  - Relation: {relation.source} -[{relation.type}]-> {relation.target}")
            runtime.emit("relation.created", {
                "id": relation.id,
                "type": relation.type,
                "source": relation.source,
                "target": relation.target,
                "data": relation.data
            })

        # Run a resolution action which succeeds
        resolution_action = "Flush DNS cache"
        print(f"Executing resolution action: '{resolution_action}'")
        runtime.emit("action.executed", {
            "action": resolution_action,
            "status": "success",
            "message": "Flushed resolver cache successfully"
        })

        # Emits a success event
        print("Emitting success event: type='run.completed'")
        runtime.emit("run.completed", {
            "status": "success",
            "message": "Successfully connected to pg database"
        })

        runtime.dispatch_loop()

    # Verify SQLite Event stream for Run 1
    run_1_events = store.get_events(run_1_id)
    print(f"SQLite Verification: Run 1 has {len(run_1_events)} events saved.")

    # =========================================================================
    # Scenario Run 2 (Memory Retrieval & Replay)
    # =========================================================================
    print("\n=== Scenario Run 2: Memory Retrieval & Replay ===")
    run_2_id = "run_2"
    run_2 = Run(
        run_id=run_2_id,
        goal="Connect to postgresql",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    store.create_run(run_2)

    with runtime.active_run(run_2_id):
        print(f"Starting run: '{run_2_id}' with goal: '{run_2.goal}'")

        log_text_2 = "Failed to connect to postgresql database on port 5432. timed out."
        print(f"Simulating connection log: '{log_text_2}'")

        # Extract entities and emit events
        entities_2, relations_2 = extractor.extract(log_text_2)
        for entity in entities_2:
            runtime.emit("object.created", {
                "id": entity.id,
                "name": entity.name,
                "type": entity.type,
                "data": entity.data
            })
        for relation in relations_2:
            runtime.emit("relation.created", {
                "id": relation.id,
                "type": relation.type,
                "source": relation.source,
                "target": relation.target,
                "data": relation.data
            })

        # Query past trajectories using memory tool
        search_names = [e.name for e in entities_2 if e.name in ["Postgres", "TimeoutError"]]
        print(f"Querying memory tool for trajectories matching: {search_names}")
        past_trajectories = memory_tool.query_past_trajectories(search_names)
        print(f"Memory retrieved: {len(past_trajectories.get('nodes', []))} nodes, {len(past_trajectories.get('relationships', []))} relationships.")

        # Inspect the returned raw path graph to identify resolution event
        replicated_action = None
        event_nodes = [n for n in past_trajectories.get("nodes", []) if "Event" in n.get("labels", [])]
        for ev_node in event_nodes:
            props = ev_node.get("properties", {})
            payload = props.get("payload", {})
            if isinstance(payload, dict) and payload.get("action") == "Flush DNS cache":
                replicated_action = payload.get("action")
                break

        if replicated_action:
            print(f"Match found! Replicating successful action from past trajectory: '{replicated_action}'")
            runtime.emit("action.executed", {
                "action": replicated_action,
                "status": "success",
                "message": "Replicated resolution action from memory"
            })
            runtime.emit("run.completed", {
                "status": "success",
                "message": "Successfully resolved database connection using past memories"
            })
        else:
            print("No matching past trajectories/solutions found in memory.")

        runtime.dispatch_loop()

    # =========================================================================
    # Scenario Run 3 (Fork-and-Diff Branching)
    # =========================================================================
    print("\n=== Scenario Run 3: Fork-and-Diff Branching ===")
    
    # Locate the timeout event in run_1
    fork_event = None
    for ev in run_1_events:
        if ev.type == "object.created" and ev.payload.get("name") == "TimeoutError":
            fork_event = ev
            break

    if not fork_event:
        # Fallback if name matching is configured differently
        for ev in run_1_events:
            if ev.type == "object.created":
                fork_event = ev
                break

    assert fork_event is not None, "Could not find a valid fork point event in run_1"

    run_3_id = "run_3"
    print(f"Forking run_1 into '{run_3_id}' at event ID: '{fork_event.id}' ({fork_event.type}: {fork_event.payload.get('name')})")

    # Perform the fork
    runtime.fork_run(
        parent_run_id=run_1_id,
        new_run_id=run_3_id,
        forked_at_event_id=fork_event.id
    )

    # Verify run_3 metadata in SQLite
    run_3_meta = store.get_run(run_3_id)
    assert run_3_meta is not None, "Forked run metadata not found"
    assert run_3_meta.parent_run_id == run_1_id, "Parent run ID mismatch"
    assert run_3_meta.forked_at_event_id == fork_event.id, "Fork point event ID mismatch"
    print("SQLite Verification passed:")
    print(f"  - Parent Run: {run_3_meta.parent_run_id}")
    print(f"  - Forked Event ID: {run_3_meta.forked_at_event_id}")

    # Verify run_3 events list in SQLite
    run_3_events = store.get_events(run_3_id)
    print(f"  - Copied event count: {len(run_3_events)}")
    fork_index_in_parent = next(i for i, ev in enumerate(run_1_events) if ev.id == fork_event.id)
    expected_event_ids = [ev.id for ev in run_1_events[:fork_index_in_parent + 1]]
    actual_event_ids = [ev.id for ev in run_3_events]
    assert actual_event_ids == expected_event_ids, "Copied events do not match the expected prefix"
    print("  - Event list matches expected prefix exactly.")

    # Verify Neo4j connection details
    if neo4j_available and projection:
        with memory_tool.driver.session() as session:
            res = session.run(
                "MATCH (r:Run {run_id: $run_id})-[:FORKED_FROM]->(e:Event) RETURN e.id AS parent_event_id",
                run_id=run_3_id
            )
            row = res.single()
            assert row is not None, "Neo4j: run node is not linked via FORKED_FROM relation"
            assert row["parent_event_id"] == fork_event.id, "Neo4j: parent event ID mismatch on FORKED_FROM relationship"
            print(f"Neo4j Verification passed: Run node '{run_3_id}' is linked to parent Event '{row['parent_event_id']}' via FORKED_FROM relationship.")
    else:
        print("Neo4j is not available; skipping Neo4j relation checks (verification logic passes offline checks).")

    # Clean up connections
    memory_tool.close()
    if projection:
        projection.close()
    store.close()

    print("\nDemo completed successfully!")


if __name__ == "__main__":
    main()
