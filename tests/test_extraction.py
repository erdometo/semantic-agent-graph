import pytest
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
    # If API key is missing (explicitly not provided) and text doesn't match regex
    extractor = EntityExtractor(api_key=None)
    assert extractor.client is None
    
    entities, relations = extractor.extract("User alice logged in from ip 192.168.1.1")
    assert entities == []
    assert relations == []


def test_llm_based_parser_mock():
    # Setup mock for google.genai client
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        # Initialize extractor with api_key so client is created
        extractor = EntityExtractor(api_key="test_api_key")
        assert extractor.client is not None
        
        # Setup mock LLM response
        from semantic_agent_graph.extraction import ExtractionResponseSchema, ExtractedEntity, ExtractedRelation
        
        mock_parsed = ExtractionResponseSchema(
            entities=[
                ExtractedEntity(id="ent_1", type="User", name="alice", data={"role": "admin"}),
                # Should normalize pg to Postgres and type to System
                ExtractedEntity(id="ent_2", type="Database", name="pg", data={}),
                # Should normalize timeout to TimeoutError and type to Error
                ExtractedEntity(id="ent_3", type="Exception", name="timeout", data={})
            ],
            relations=[
                ExtractedRelation(id="r1", type="ACCESSED", source="ent_1", target="ent_2", data={}),
                ExtractedRelation(id="r2", type="ENCOUNTERED", source="ent_2", target="ent_3", data={})
            ]
        )
        
        mock_response = MagicMock()
        mock_response.parsed = mock_parsed
        mock_response.text = '{"entities": [], "relations": []}' # fallback text
        
        extractor.client.models.generate_content.return_value = mock_response
        
        # This text does NOT match the connection log regex condition
        text = "User alice logged in from ip 192.168.1.1 and started process 999."
        entities, relations = extractor.extract(text)
        
        # Verify generate_content was called
        extractor.client.models.generate_content.assert_called_once()
        
        # Verify entities normalized correctly
        assert len(entities) == 3
        # ent_1: alice -> name is alice, type is User (not canonicalized)
        assert any(e.name == "alice" and e.type == "User" and e.id == "alice" for e in entities)
        # ent_2: pg -> name normalized to Postgres, type to System
        assert any(e.name == "Postgres" and e.type == "System" and e.id == "Postgres" for e in entities)
        # ent_3: timeout -> name normalized to TimeoutError, type to Error
        assert any(e.name == "TimeoutError" and e.type == "Error" and e.id == "TimeoutError" for e in entities)
        
        # Verify relations updated IDs to normalized names
        assert len(relations) == 2
        # r1: ent_1 -> ent_2 maps to alice -> Postgres
        assert any(r.type == "ACCESSED" and r.source == "alice" and r.target == "Postgres" for r in relations)
        # r2: ent_2 -> ent_3 maps to Postgres -> TimeoutError
        assert any(r.type == "ENCOUNTERED" and r.source == "Postgres" and r.target == "TimeoutError" for r in relations)


def test_llm_based_parser_fallback_to_text():
    # Test case where response.parsed is None, but response.text has JSON string
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        
        extractor = EntityExtractor(api_key="test_api_key")
        
        mock_response = MagicMock()
        mock_response.parsed = None
        mock_response.text = '{"entities": [{"id": "sys", "type": "db", "name": "postgresql"}], "relations": []}'
        
        extractor.client.models.generate_content.return_value = mock_response
        
        entities, relations = extractor.extract("Non-matching regex text message")
        
        assert len(entities) == 1
        assert entities[0].name == "Postgres"
        assert entities[0].type == "System"
        assert entities[0].id == "Postgres"
        assert len(relations) == 0
