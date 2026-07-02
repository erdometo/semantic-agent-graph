import os
import json
import uuid
import datetime
import logging
from typing import Optional
from semantic_agent_graph.models import Event, Run
from semantic_agent_graph.store import SQLiteEventStore
from semantic_agent_graph.projection import Neo4jProjection
from semantic_agent_graph.extraction import EntityExtractor

logger = logging.getLogger(__name__)

# A realistic SWE-agent trajectory representing the resolution of a django queryset AttributeError
DEFAULT_SAMPLE_TRAJECTORY = {
    "trajectory": [
        {
            "thought": "Let's first explore the repository to locate queryset modules.",
            "action": "find_files django/db/models/query.py",
            "observation": "Found: django/db/models/query.py"
        },
        {
            "thought": "The issue mentions an AttributeError in query evaluation. Let's read query.py where filters are processed.",
            "action": "view_file django/db/models/query.py 120 150",
            "observation": "Line 132: def filter(self, *args, **kwargs):\nLine 133:     # Raises AttributeError if timezone tzinfo is None"
        },
        {
            "thought": "Let's run pytests to reproduce the traceback.",
            "action": "pytest tests/test_query.py",
            "observation": "pytest Version 8.0.0\nFAIL tests/test_query.py::test_timezone_filter\nAttributeError: 'NoneType' object has no attribute 'tzinfo'"
        },
        {
            "thought": "We found the bug. It occurs when filtering with timezone-naive values. Let's edit query.py to handle None values.",
            "action": "edit django/db/models/query.py",
            "observation": "Applied timezone patch successfully."
        },
        {
            "thought": "Now let's run pytest again to verify the bug is resolved.",
            "action": "pytest tests/test_query.py",
            "observation": "pytest Version 8.0.0\n13 passed in 0.22s\nSUCCESS"
        },
        {
            "thought": "The fix is verified. Let's submit the solution.",
            "action": "submit",
            "observation": "PR created: resolved AttributeError in django QuerySet timezone evaluation."
        }
    ]
}

def create_sample_trajectory_file(file_path: str = "sample_swe_trajectory.json"):
    """
    Creates a sample SWE-agent trajectory JSON file for demonstration.
    """
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_SAMPLE_TRAJECTORY, f, indent=2)
    print(f"Created sample SWE-agent trajectory file: {file_path}")

def ingest_swe_trajectory(
    store: SQLiteEventStore,
    projection: Optional[Neo4jProjection],
    extractor: EntityExtractor,
    traj_path: str,
    run_id: str,
    goal: str,
    is_success: bool = True
) -> Run:
    """
    Parses a SWE-agent trajectory JSON file and ingests it into SQLite and Neo4j.
    """
    if not os.path.exists(traj_path):
        raise FileNotFoundError(f"Trajectory file not found at: {traj_path}")
        
    with open(traj_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    trajectory = data.get("trajectory", [])
    if not trajectory:
        raise ValueError("Invalid trajectory format: 'trajectory' list is empty or missing.")
        
    # 1. Create the Run
    run = Run(
        run_id=run_id,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        goal=goal
    )
    store.create_run(run)
    events_to_project = []
    
    if projection:
        # Buffer run node projection
        events_to_project.append(Event(
            id=str(uuid.uuid4()),
            type="run.created",
            timestamp=run.created_at,
            run_id=run_id,
            payload={"run_id": run_id, "goal": goal}
        ))
        
    print(f"Ingesting Run: {run_id} | Goal: {goal}")
    print(f"Found {len(trajectory)} steps in trajectory file.")
    
    # 2. Loop through each step in the trajectory list
    prev_event_id = None
    for idx, step in enumerate(trajectory):
        thought = step.get("thought", "")
        action = step.get("action", "")
        observation = step.get("observation", "")
        
        # Emit the main step event
        step_event_id = str(uuid.uuid4())
        step_event = Event(
            id=step_event_id,
            type="agent.step",
            actor="agent",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            run_id=run_id,
            payload={
                "thought": thought,
                "action": action,
                "observation": observation,
                "step_index": idx
            },
            caused_by=prev_event_id
        )
        
        # Append step event
        store.append_event(step_event)
        events_to_project.append(step_event)
            
        print(f"Step {idx}: Executed '{action}'")
        
        # Extract Entities and Relations from action and observation
        entities, relations = extractor.extract(f"Action: {action}\nObservation: {observation}")
        
        # Project extracted entities in Neo4j
        for ent in entities:
            # Emit object.created event to store and projection
            ent_event = Event(
                id=str(uuid.uuid4()),
                type="object.created",
                actor="extractor",
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                run_id=run_id,
                payload={
                    "id": ent.id,
                    "type": ent.type,
                    "name": ent.name,
                    "data": ent.data
                },
                caused_by=step_event_id
            )
            store.append_event(ent_event)
            events_to_project.append(ent_event)
                
        # Project extracted relationships in Neo4j
        for rel in relations:
            # Emit relation.created event
            rel_event = Event(
                id=str(uuid.uuid4()),
                type="relation.created",
                actor="extractor",
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                run_id=run_id,
                payload={
                    "id": rel.id,
                    "type": rel.type,
                    "source": rel.source,
                    "target": rel.target,
                    "data": rel.data
                },
                caused_by=step_event_id
            )
            store.append_event(rel_event)
            events_to_project.append(rel_event)
                
        prev_event_id = step_event_id
        
    # Emit final completion/failure event
    if is_success:
        terminal_event = Event(
            id=str(uuid.uuid4()),
            type="run.completed",
            actor="agent",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            run_id=run_id,
            payload={"status": "success"},
            caused_by=prev_event_id
        )
    else:
        terminal_event = Event(
            id=str(uuid.uuid4()),
            type="run.failed",
            actor="agent",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            run_id=run_id,
            payload={"status": "failed", "message": "Failed to resolve GitHub issue."},
            caused_by=prev_event_id
        )
    store.append_event(terminal_event)
    events_to_project.append(terminal_event)
    
    if projection:
        projection.apply_events(events_to_project)
        
    print(f"Run {run_id} successfully ingested and bloomed!")
    return run

if __name__ == "__main__":
    # Test script locally
    import sys
    # Initialize in-memory store
    store = SQLiteEventStore(":memory:")
    extractor = EntityExtractor()
    
    # Try connecting to Neo4j
    projection = None
    try:
        projection = Neo4jProjection()
        projection.clear_db()
        print("Connected to Neo4j database.")
    except Exception:
        print("Neo4j database not available. Running ingestion in SQLite-only mode.")
        projection = None
        
    traj_file = "sample_swe_trajectory.json"
    if not os.path.exists(traj_file):
        create_sample_trajectory_file(traj_file)
        
    ingest_swe_trajectory(
        store=store,
        projection=projection,
        extractor=extractor,
        traj_path=traj_file,
        run_id="swe_run_demo_1",
        goal="Resolve timezone AttributeError in django QuerySet."
    )
    
    # Close connections
    if projection:
        projection.close()
