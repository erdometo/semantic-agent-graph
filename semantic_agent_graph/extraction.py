import logging
import re
from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from semantic_agent_graph.models import Entity, Relation

logger = logging.getLogger(__name__)

# Canonical name normalization lookup maps
CANONICAL_LOOKUP = {
    # Systems
    "pg": "Postgres",
    "postgres": "Postgres",
    "postgresql": "Postgres",
    "mysql": "MySQL",
    "redis": "Redis",
    "mongodb": "MongoDB",
    "mongo": "MongoDB",
    
    # Configurations
    "5432": "Port 5432",
    "port 5432": "Port 5432",
    "port:5432": "Port 5432",
    "port=5432": "Port 5432",
    "port5432": "Port 5432",
    
    # Errors
    "timeout": "TimeoutError",
    "timeouterror": "TimeoutError",
    "connectiontimeout": "TimeoutError",
    "connection_timeout": "TimeoutError",
    "conn_timeout": "TimeoutError",
    "timed out": "TimeoutError",
    "timed_out": "TimeoutError",
}

CANONICAL_TYPES = {
    "Postgres": "System",
    "MySQL": "System",
    "Redis": "System",
    "MongoDB": "System",
    "Port 5432": "Configuration",
    "TimeoutError": "Error",
}


def normalize_name(name: str) -> str:
    cleaned = name.strip().lower()
    # Normalize internal spaces/punctuation for key lookup
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return CANONICAL_LOOKUP.get(cleaned, name)


def get_canonical_type(normalized_name: str, original_type: str) -> str:
    return CANONICAL_TYPES.get(normalized_name, original_type)


class ExtractedEntity(BaseModel):
    id: str = Field(..., description="Unique identifier for the entity (e.g., 'postgres', 'port_5432')")
    type: str = Field(..., description="The type or label of the entity (e.g., 'System', 'Configuration', 'Error', 'User')")
    name: str = Field(..., description="The name/value of the entity")
    data: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary properties/metadata associated with the entity")


class ExtractedRelation(BaseModel):
    id: str = Field(..., description="Unique identifier for the relationship")
    type: str = Field(..., description="The type/label of relation (e.g., 'CONFIGURED_WITH', 'ENCOUNTERED')")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    data: Dict[str, Any] = Field(default_factory=dict, description="Properties associated with the relationship")


class ExtractionResponseSchema(BaseModel):
    entities: List[ExtractedEntity] = Field(default_factory=list, description="List of entities extracted from the log text")
    relations: List[ExtractedRelation] = Field(default_factory=list, description="List of relationships between the extracted entities")


class EntityExtractor:
    def __init__(self, api_key: Optional[str] = None):
        self.client = None
        import os
        effective_key = api_key or os.environ.get("GEMINI_API_KEY")
        if effective_key:
            if genai is None:
                logger.error("google-genai package is not installed or failed to import.")
            else:
                self.client = genai.Client(api_key=effective_key)

    def extract(self, text: str) -> Tuple[List[Entity], List[Relation]]:
        """
        Resolves entities using a hybrid approach:
        - First attempts to match known standard patterns using a Regex parser.
        - Falls back to an LLM-based parser if regex doesn't match and the Gemini client is initialized.
        - Returns empty lists if neither can parse the text.
        - Applies canonical naming normalization to all extracted entities and adjusts relationships accordingly.
        """
        # 1. Try regex-based parser
        entities, relations = self._extract_via_regex(text)
        if entities:
            return entities, relations

        # 2. Try LLM-based parser if client is initialized
        if self.client is not None:
            try:
                return self._extract_via_llm(text)
            except Exception as e:
                logger.error(f"Error during LLM extraction: {e}")
                return [], []

        # 3. Fallback when client is not initialized and regex didn't match
        return [], []

    def _extract_via_regex(self, text: str) -> Tuple[List[Entity], List[Relation]]:
        # Regex patterns for connection logs
        sys_pattern = re.compile(r"\b(postgres|postgresql|pg)\b", re.IGNORECASE)
        config_pattern = re.compile(r"\b(port\s*[:=]?\s*5432|5432)\b", re.IGNORECASE)
        error_pattern = re.compile(r"\b(timeouterror|connectiontimeout|timeout|timed\s*out)\b", re.IGNORECASE)

        sys_match = sys_pattern.search(text)
        config_match = config_pattern.search(text)
        error_match = error_pattern.search(text)

        # Trigger regex parsing if we see connection log hallmarks:
        # Must have a system match, and at least one of config or error match.
        if sys_match and (config_match or error_match):
            entities = []
            relations = []

            sys_raw = sys_match.group(1)
            sys_name = normalize_name(sys_raw)
            sys_type = get_canonical_type(sys_name, "System")
            sys_entity = Entity(id=sys_name, type=sys_type, name=sys_name, data={})
            entities.append(sys_entity)

            cfg_entity = None
            if config_match:
                cfg_raw = config_match.group(1)
                cfg_name = normalize_name(cfg_raw)
                cfg_type = get_canonical_type(cfg_name, "Configuration")
                cfg_entity = Entity(id=cfg_name, type=cfg_type, name=cfg_name, data={})
                entities.append(cfg_entity)

                relations.append(Relation(
                    id=f"{sys_name}-CONFIGURED_WITH-{cfg_name}",
                    type="CONFIGURED_WITH",
                    source=sys_name,
                    target=cfg_name,
                    data={}
                ))

            err_entity = None
            if error_match:
                err_raw = error_match.group(1)
                err_name = normalize_name(err_raw)
                err_type = get_canonical_type(err_name, "Error")
                err_entity = Entity(id=err_name, type=err_type, name=err_name, data={})
                entities.append(err_entity)

                relations.append(Relation(
                    id=f"{sys_name}-ENCOUNTERED-{err_name}",
                    type="ENCOUNTERED",
                    source=sys_name,
                    target=err_name,
                    data={}
                ))

            return entities, relations

        return [], []

    def _extract_via_llm(self, text: str) -> Tuple[List[Entity], List[Relation]]:
        prompt_text = (
            f"Analyze the following developer/system log text and extract all semantic entities "
            f"and their relations.\n\n"
            f"Log Text:\n{text}\n\n"
            f"Provide the response as JSON adhering strictly to the schema provided. "
            f"Make sure that any relation's source and target match the ID of an extracted entity."
        )

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractionResponseSchema,
            system_instruction=(
                "You are an expert system that extracts structured entities and relations from developer "
                "and system logs. Identify systems, errors, configurations, components, users, services, "
                "or IPs, and link them using descriptive relations (e.g. 'CONFIGURED_WITH', 'ENCOUNTERED', "
                "'CONNECTED_FROM', 'CALLED')."
            )
        )

        response = self.client.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt_text,
            config=config
        )

        raw_result = None
        # Try response.parsed if google-genai supports it, or parse response.text manually
        if hasattr(response, 'parsed') and response.parsed is not None:
            raw_result = response.parsed
        elif response.text:
            raw_result = ExtractionResponseSchema.model_validate_json(response.text)
        else:
            return [], []

        entities = []
        relations = []
        id_mapping = {}

        # Normalize and construct Entity objects
        for ent in raw_result.entities:
            normalized_name = normalize_name(ent.name)
            normalized_type = get_canonical_type(normalized_name, ent.type)
            # Use normalized name as canonical ID to align with regex extractor patterns
            normalized_id = normalized_name
            
            id_mapping[ent.id] = normalized_id

            entities.append(Entity(
                id=normalized_id,
                type=normalized_type,
                name=normalized_name,
                data=ent.data or {}
            ))

        # Normalize relations
        for rel in raw_result.relations:
            mapped_source = id_mapping.get(rel.source, rel.source)
            mapped_target = id_mapping.get(rel.target, rel.target)
            new_rel_id = f"{mapped_source}-{rel.type}-{mapped_target}"

            relations.append(Relation(
                id=new_rel_id,
                type=rel.type,
                source=mapped_source,
                target=mapped_target,
                data=rel.data or {}
            ))

        # Deduplicate entities
        seen_entities = {}
        for ent in entities:
            if ent.id not in seen_entities:
                seen_entities[ent.id] = ent
        entities = list(seen_entities.values())

        # Deduplicate relations
        seen_relations = {}
        for rel in relations:
            if rel.id not in seen_relations:
                seen_relations[rel.id] = rel
        relations = list(seen_relations.values())

        return entities, relations
