import pytest
import json
from unittest.mock import MagicMock, patch
from semantic_agent_graph.extraction import EntityExtractor, normalize_name, get_canonical_type
from semantic_agent_graph.models import Entity, Relation


def test_normalization_helpers():
    # Test normalization of names
    assert normalize_name("pg") == "Postgres"
    assert normalize_name("postgres") == "Postgres"
    assert normalize_name("postgresql") == "Postgres"
    assert normalize_name("  PostgreSQL  ") == "Postgres"
    
    assert normalize_name("5432") == "Port 5432"
    assert normalize_name("port 5432") == "Port 5432"
    assert normalize_name("port:5432") == "Port 5432"
    
    assert normalize_name("timeout") == "TimeoutError"
    assert normalize_name("timeouterror") == "TimeoutError"
    assert normalize_name("connectiontimeout") == "TimeoutError"
    assert normalize_name("timed out") == "TimeoutError"
    
    # Non-lookup strings should remain as-is (except stripped/original)
    assert normalize_name("CustomService") == "CustomService"
    
    # Test canonical types
    assert get_canonical_type("Postgres", "Unknown") == "System"
    assert get_canonical_type("Port 5432", "Unknown") == "Configuration"
    assert get_canonical_type("TimeoutError", "Unknown") == "Error"
    assert get_canonical_type("CustomService", "Service") == "Service"


def test_regex_matching_connection_logs():
    extractor = EntityExtractor()
    
    # Test full connection log
    text = "Connection to postgresql on port 5432 failed with TimeoutError"
    entities, relations = extractor.extract(text)
    
    # Verify correct extraction
    assert len(entities) == 3
    assert any(e.name == "Postgres" and e.type == "System" for e in entities)
    assert any(e.name == "Port 5432" and e.type == "Configuration" for e in entities)
    assert any(e.name == "TimeoutError" and e.type == "Error" for e in entities)
    
    assert len(relations) == 2
    assert any(r.type == "CONFIGURED_WITH" and r.source == "Postgres" and r.target == "Port 5432" for r in relations)
    assert any(r.type == "ENCOUNTERED" and r.source == "Postgres" and r.target == "TimeoutError" for r in relations)


def test_regex_matching_partial_logs():
    extractor = EntityExtractor()
    
    # Test system + error only
    text = "pg database connection timeout occurred"
    entities, relations = extractor.extract(text)
    
    assert len(entities) == 2
    assert any(e.name == "Postgres" and e.type == "System" for e in entities)
    assert any(e.name == "TimeoutError" and e.type == "Error" for e in entities)
    
    assert len(relations) == 1
    assert relations[0].type == "ENCOUNTERED"
    assert relations[0].source == "Postgres"
    assert relations[0].target == "TimeoutError"
    
    # Test system + config only
    text = "connect to postgres at port 5432 successfully"
    entities, relations = extractor.extract(text)
    
    assert len(entities) == 2
    assert any(e.name == "Postgres" and e.type == "System" for e in entities)
    assert any(e.name == "Port 5432" and e.type == "Configuration" for e in entities)
    
    assert len(relations) == 1
    assert relations[0].type == "CONFIGURED_WITH"
    assert relations[0].source == "Postgres"
    assert relations[0].target == "Port 5432"


def test_fallback_behavior_no_api_key():
    # If API key is missing and text doesn't match regex, return empty lists
    extractor = EntityExtractor(api_key=None)
    assert extractor.openrouter_key is None
    
    entities, relations = extractor.extract("User alice logged in from ip 192.168.1.1")
    assert entities == []
    assert relations == []


@patch("urllib.request.urlopen")
def test_openrouter_based_parser_mock(mock_urlopen):
    # Mock return value for urllib.request.urlopen
    mock_response_obj = MagicMock()
    
    inner_json = {
        "entities": [
            {"id": "ent_1", "type": "User", "name": "alice", "data": {"role": "admin"}},
            # Should normalize pg to Postgres and type to System
            {"id": "ent_2", "type": "Database", "name": "pg", "data": {}},
            # Should normalize timeout to TimeoutError and type to Error
            {"id": "ent_3", "type": "Exception", "name": "timeout", "data": {}}
        ],
        "relations": [
            {"id": "r1", "type": "ACCESSED", "source": "ent_1", "target": "ent_2", "data": {}},
            {"id": "r2", "type": "ENCOUNTERED", "source": "ent_2", "target": "ent_3", "data": {}}
        ]
    }
    
    openrouter_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(inner_json)
                }
            }
        ]
    }
    
    mock_response_obj.read.return_value = json.dumps(openrouter_response).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response_obj
    
    # Initialize extractor with api_key so client is active
    extractor = EntityExtractor(api_key="test_openrouter_key")
    assert extractor.openrouter_key == "test_openrouter_key"
    
    # Non-matching regex text
    text = "User alice logged in from ip 192.168.1.1 and started process 999."
    entities, relations = extractor.extract(text)
    
    # Verify urlopen was called once
    mock_urlopen.assert_called_once()
    
    # Verify entities normalized correctly
    assert len(entities) == 3
    assert any(e.name == "alice" and e.type == "User" and e.id == "alice" for e in entities)
    assert any(e.name == "Postgres" and e.type == "System" and e.id == "Postgres" for e in entities)
    assert any(e.name == "TimeoutError" and e.type == "Error" and e.id == "TimeoutError" for e in entities)
    
    # Verify relations updated IDs to normalized names
    assert len(relations) == 2
    assert any(r.type == "ACCESSED" and r.source == "alice" and r.target == "Postgres" for r in relations)
    assert any(r.type == "ENCOUNTERED" and r.source == "Postgres" and r.target == "TimeoutError" for r in relations)
