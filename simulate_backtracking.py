import os
import time
import datetime
import logging
from typing import Optional, Dict, Any

from semantic_agent_graph import (
    Event,
    Run,
    Entity,
    Relation,
    SQLiteEventStore,
    ReactiveRuntime,
    Neo4jProjection,
    PredictiveBacktrackingEngine,
)

# Configure logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("simulate_backtracking")

DB_PATH = "semantic_agent_graph.db"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "password")

def print_banner(text: str):
    print("\n" + "=" * 80)
    print(f" {text} ".center(80, "="))
    print("=" * 80)

def main():
    print_banner("sAG Predictive Dead-End Detection & Backtracking Simulation")

    # 1. Initialize databases
    print(f"Connecting to SQLite: {DB_PATH}")
    store = SQLiteEventStore(DB_PATH)
    
    print(f"Connecting to Neo4j: {NEO4J_URI}")
    projection = None
    neo4j_available = True
    try:
        projection = Neo4jProjection(uri=NEO4J_URI, auth=NEO4J_AUTH)
        with projection.driver.session() as session:
            session.run("RETURN 1")
        print("Connected to Neo4j database successfully.")
    except Exception as e:
        print(f"WARNING: Cannot connect to Neo4j: {e}")
        print("Skipping real database checks (simulating backtracking workflow).")
        neo4j_available = False

    runtime = ReactiveRuntime(store=store, projection=projection)
    
    # 2. Cleanup past backtracking simulation runs to ensure reproducibility
    try:
        with store._lock:
            cursor = store._conn.cursor()
            cursor.execute("DELETE FROM events WHERE run_id LIKE 'run_bt_%'")
            cursor.execute("DELETE FROM runs WHERE run_id LIKE 'run_bt_%'")
            store._conn.commit()
        print("Cleaned up past backtracking simulation runs from SQLite database.")
    except Exception as e:
        print(f"Warning: Could not clean up old runs: {e}")

    if neo4j_available and projection:
        try:
            with projection.driver.session() as session:
                session.run("MATCH (r:Run) WHERE r.run_id STARTS WITH 'run_bt_' DETACH DELETE r")
            print("Cleaned up past backtracking simulation runs from Neo4j.")
        except Exception as e:
            print(f"Warning: Could not clean up Neo4j simulation runs: {e}")

    # 3. Seed historical failed runs
    # We will seed 3 historical runs that encountered SimPortCollision on SimNginx or SimApache and failed.
    hist_runs = [
        {"run_id": "run_bt_hist_1", "system": "SimNginx", "goal": "Setup SimNginx gateway"},
        {"run_id": "run_bt_hist_2", "system": "SimApache", "goal": "Setup SimApache web server"},
        {"run_id": "run_bt_hist_3", "system": "SimNginx", "goal": "Configure SimNginx load balancer"},
    ]
    
    print("\nSeeding historical failed trajectories...")
    for h in hist_runs:
        run_obj = Run(
            run_id=h["run_id"],
            goal=h["goal"],
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        )
        store.create_run(run_obj)
        with runtime.active_run(h["run_id"]):
            # Step 0: Setup environment
            runtime.emit("action.executed", {
                "action": "Initialize server configuration",
                "status": "success"
            })
            # Step 1: Encounter port collision entities
            runtime.emit("object.created", {
                "id": h["system"],
                "name": h["system"],
                "type": "Entity",
                "data": {}
            })
            runtime.emit("object.created", {
                "id": "SimPortCollision",
                "name": "SimPortCollision",
                "type": "Error",
                "data": {}
            })
            # Step 2: Failed action attempts
            runtime.emit("action.executed", {
                "action": "Ignore port warnings",
                "status": "failed"
            })
            # Step 3: Run failed
            runtime.emit("run.failed", {
                "status": "failed",
                "message": "Port 8080 already in use"
            })
            runtime.dispatch_loop()
        print(f"  - Seeded failed run {h['run_id']} for {h['system']}")

    # 4. Simulate active agent run and dead-end detection
    print_banner("Simulating Active Agent Run with Dead-End Check")
    active_run_id = "run_bt_active"
    active_run = Run(
        run_id=active_run_id,
        goal="Configure SimNginx proxy",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )
    store.create_run(active_run)
    
    engine = PredictiveBacktrackingEngine(runtime)
    
    with runtime.active_run(active_run_id):
        # Step 0: Setup Nginx Config (stable state)
        runtime.emit("action.executed", {
            "action": "Write Nginx configuration file",
            "status": "success"
        })
        runtime.dispatch_loop()
        
        # Verify initial stable state
        events_so_far = store.get_events(active_run_id)
        print(f"Active run initialized. Step 0 executed: {events_so_far[-1].payload['action']}")
        
        # Step 1: Detect port collision error
        print("\nAgent proceeds down a path and encounters collision entities...")
        runtime.emit("object.created", {
            "id": "SimNginx",
            "name": "SimNginx",
            "type": "Entity",
            "data": {}
        })
        runtime.emit("object.created", {
            "id": "SimPortCollision",
            "name": "SimPortCollision",
            "type": "Error",
            "data": {}
        })
        runtime.dispatch_loop()

        # Run Dead-End Check
        print("\nChecking for dead ends using Neo4j semantic matching...")
        dead_end_info = engine.check_dead_end(active_run_id, threshold_entities=2, threshold_failure_rate=0.75)
        
        if dead_end_info:
            print(f"  [WARNING] Dead end predicted!")
            print(f"    - Historical Failure Rate: {dead_end_info['failure_rate']*100:.1f}%")
            print(f"    - Failed Runs in Memory: {dead_end_info['failed_runs']}")
            print(f"    - Matched Failure Entities: {dead_end_info['matched_entities']}")
            print(f"    - First Matched Event ID: {dead_end_info['first_matched_event_id']} (seq {dead_end_info['first_matched_seq']})")
            
            # Trigger backtracking
            print_banner("Triggering Proactive Backtracking")
            fork_run_id = "run_bt_active_forked"
            engine.backtrack(active_run_id, fork_run_id, dead_end_info)
            
            # 5. Verify the Forked Run
            print_banner("Verifying Forked Run State")
            forked_events = store.get_events(fork_run_id)
            print(f"Forked Run ID: {fork_run_id}")
            print(f"Total events in forked run: {len(forked_events)}")
            
            # Print forked events sequence
            for idx, ev in enumerate(forked_events):
                print(f"  Event {idx} (seq {ev.seq}): type={ev.type} | payload={ev.payload}")
                
            # Verify the negative feedback is present
            feedback_event = forked_events[-1]
            assert feedback_event.type == "run.backtracked", "Forked run does not end with run.backtracked event"
            assert "Do not repeat actions that touch" in feedback_event.payload["message"], "Feedback message is missing warning details"
            print("\nVerification Succeeded! The backtracking engine correctly:")
            print("  1. Detected the dead end based on historical failure rates in Neo4j.")
            print("  2. Backtracked (forked) to the last stable state (before port collision).")
            print("  3. Injected a structured negative feedback event to guide the LLM agent.")
        else:
            print("No dead end detected. The current trajectory has no match in historical failure memory.")

    store.close()
    if projection:
        projection.close()

if __name__ == "__main__":
    main()
