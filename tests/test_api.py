import pytest
import json
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from semantic_agent_graph.api import app, lifecycle
from semantic_agent_graph.store import SQLiteEventStore
from semantic_agent_graph.models import Run, Event

client = TestClient(app)

class MockRecord(dict):
    def __getitem__(self, key):
        return super().get(key)

@pytest.fixture
def mock_api_lifecycle():
    # Save original references
    orig_store = lifecycle.store
    orig_proj = lifecycle.projection

    # Setup temporary SQLite event store in memory
    test_store = SQLiteEventStore(":memory:")
    lifecycle.store = test_store

    # Mock Neo4j projection and driver
    mock_projection = MagicMock()
    mock_driver = MagicMock()
    mock_session = MagicMock()
    
    mock_projection.driver = mock_driver
    mock_driver.session.return_value.__enter__.return_value = mock_session
    lifecycle.projection = mock_projection

    yield mock_session, test_store

    # Cleanup and restore
    test_store.close()
    lifecycle.store = orig_store
    lifecycle.projection = orig_proj

def test_api_stats_endpoint(mock_api_lifecycle):
    mock_session, test_store = mock_api_lifecycle
    
    # 1. Seed database with a run and an event
    run = Run(run_id="run_1", goal="Test goal", created_at="2026-06-26T20:00:00Z")
    test_store.create_run(run)
    event = Event(
        id="evt_1",
        run_id="run_1",
        seq=1,
        type="agent.step",
        payload={"action": "ls"},
        actor="agent",
        timestamp="2026-06-26T20:00:01Z"
    )
    test_store.append_event(event)

    # 2. Mock Neo4j counts queries
    # Query 1: MATCH (n) RETURN labels(n)[0] AS label, count(n) AS cnt
    # Query 2: MATCH ()-[r]->() RETURN count(r) AS cnt
    mock_result_nodes = [
        MockRecord(label="Run", cnt=2),
        MockRecord(label="Event", cnt=5),
        MockRecord(label="Entity", cnt=3)
    ]
    mock_result_rels = MagicMock()
    mock_result_rels.single.return_value = MockRecord(cnt=10)

    def mock_run(query, **kwargs):
        if "labels(n)" in query:
            return mock_result_nodes
        elif "count(r)" in query:
            return mock_result_rels
        return []

    mock_session.run.side_effect = mock_run

    # 3. Call endpoint
    response = client.get("/api/stats")
    assert response.status_code == 200
    
    data = response.json()
    assert data["neo4j_available"] is True
    assert data["sqlite"]["runs"] == 1
    assert data["sqlite"]["events"] == 1
    assert data["neo4j"]["runs"] == 2
    assert data["neo4j"]["events"] == 5
    assert data["neo4j"]["entities"] == 3
    assert data["neo4j"]["relationships"] == 10

def test_api_runs_endpoint(mock_api_lifecycle):
    _, test_store = mock_api_lifecycle
    
    # Seed runs
    test_store.create_run(Run(run_id="run_1", goal="Goal 1", created_at="2026-06-26T20:00:00Z"))
    test_store.create_run(Run(run_id="run_2", goal="Goal 2", parent_run_id="run_1", created_at="2026-06-26T20:01:00Z"))

    response = client.get("/api/runs")
    assert response.status_code == 200
    
    data = response.json()
    assert len(data) == 2
    assert data[0]["run_id"] == "run_2"
    assert data[1]["run_id"] == "run_1"
    assert data[0]["parent_run_id"] == "run_1"

def test_api_run_graph_endpoint(mock_api_lifecycle):
    mock_session, _ = mock_api_lifecycle

    # Define mock returns for the run graph queries
    mock_run_node = MockRecord(r={"run_id": "run_1", "goal": "Connect to pg"})
    mock_events = [
        MockRecord(e={"id": "evt_1", "type": "agent.step", "seq": 1, "actor": "agent", "payload": '{"action": "test"}'}),
        MockRecord(e={"id": "evt_2", "type": "run.completed", "seq": 2, "actor": "system", "payload": "{}"})
    ]
    mock_next_links = [
        MockRecord(source="evt_1", target="evt_2", type="NEXT")
    ]

    def mock_run(query, **kwargs):
        res = MagicMock()
        if "MATCH (r:Run {run_id: $run_id}) RETURN r" in query:
            res.single.return_value = mock_run_node
            return res
        elif "MATCH (r:Run {run_id: $run_id})-[:CONTAINS]->(e:Event)" in query:
            return mock_events
        elif "MATCH (prev:Event {run_id: $run_id})-[rel:NEXT]->" in query:
            return mock_next_links
        # Return empty list for forks, caused_by and entity matches to keep it simple
        return []

    mock_session.run.side_effect = mock_run

    response = client.get("/api/runs/run_1/graph")
    assert response.status_code == 200
    
    data = response.json()
    assert "nodes" in data
    assert "links" in data
    
    # 1 Run node + 2 Event nodes = 3 nodes
    assert len(data["nodes"]) == 3
    # 2 CONTAINS links + 1 NEXT link = 3 links
    assert len(data["links"]) == 3

    # Verify run node structure
    run_nodes = [n for n in data["nodes"] if n["group"] == "Run"]
    assert len(run_nodes) == 1
    assert run_nodes[0]["id"] == "run_1"

    # Verify event nodes structure
    event_nodes = [n for n in data["nodes"] if n["group"] == "Event"]
    assert len(event_nodes) == 2
    assert event_nodes[0]["id"] == "evt_1"
    assert event_nodes[0]["payload"]["action"] == "test"

def test_api_semantic_graph_endpoint(mock_api_lifecycle):
    mock_session, _ = mock_api_lifecycle

    # Define mock return nodes and relationships for the semantic graph
    mock_entities = [
        MockRecord(ent={"name": "Postgres", "type": "Database", "data": '{"port": 5432}'}),
        MockRecord(ent={"name": "Port 5432", "type": "Configuration", "data": "{}"})
    ]
    mock_rels = [
        MockRecord(source="Postgres", target="Port 5432", type="CONFIGURED_WITH", data='{"reason": "default"}')
    ]

    def mock_run(query, **kwargs):
        if "MATCH (ent:Entity) RETURN ent" in query:
            return mock_entities
        elif "MATCH (s:Entity)-[r]->(t:Entity)" in query:
            return mock_rels
        return []

    mock_session.run.side_effect = mock_run

    response = client.get("/api/semantic/graph")
    assert response.status_code == 200
    
    data = response.json()
    assert len(data["nodes"]) == 2
    assert len(data["links"]) == 1
    
    assert data["nodes"][0]["id"] == "Postgres"
    assert data["nodes"][0]["data"]["port"] == 5432
    assert data["links"][0]["source"] == "Postgres"
    assert data["links"][0]["target"] == "Port 5432"
    assert data["links"][0]["type"] == "CONFIGURED_WITH"
