import os
import json
import pytest
from semantic_agent_graph import SQLiteEventStore, EntityExtractor
from semantic_agent_graph.parser_swe import ingest_swe_trajectory, create_sample_trajectory_file

def test_swe_trajectory_ingestion(tmp_path):
    # Setup paths
    db_path = str(tmp_path / "test_ingest.db")
    traj_path = str(tmp_path / "test_traj.json")
    
    # Initialize store and extractor
    store = SQLiteEventStore(db_path)
    extractor = EntityExtractor() # Offline regex fallback mode by default
    
    # Create sample file
    create_sample_trajectory_file(traj_path)
    assert os.path.exists(traj_path)
    
    # Run ingestion without Neo4j (projection=None)
    run_id = "test_run_swe_1"
    goal = "Test query set ingestion"
    run = ingest_swe_trajectory(
        store=store,
        projection=None,
        extractor=extractor,
        traj_path=traj_path,
        run_id=run_id,
        goal=goal
    )
    
    assert run.run_id == run_id
    assert run.goal == goal
    
    # Verify database contents
    db_run = store.get_run(run_id)
    assert db_run is not None
    assert db_run.goal == goal
    
    # Verify events
    events = store.get_events(run_id)
    # 6 steps + 1 completion event = 7 events (plus optional entity/relation events emitted by extractor)
    # The default sample has 6 steps. Step 2 pytest extracts Postgres, Port 5432, TimeoutError (3 object.created, 2 relation.created events)
    # Step 0 find_files extracts Postgres. Step 1 view_file extracts Postgres. Step 3 edit extracts Postgres. Step 4 pytest extracts Postgres.
    assert len(events) >= 7
    
    # Check that agent.step events have correct action payload
    step_events = [e for e in events if e.type == "agent.step"]
    assert len(step_events) == 6
    assert step_events[0].payload["action"] == "find_files django/db/models/query.py"
    
    # Check caused_by linkages
    for i in range(1, 6):
        assert step_events[i].caused_by == step_events[i-1].id
        
    store.close()
