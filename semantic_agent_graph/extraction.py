import logging
import re
import os
import json
import sys
import time
import http.client
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any, Tuple
from pydantic import BaseModel, Field

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
        """
        Initializes the EntityExtractor.
        Loads the OpenRouter API key from the parameter or the OPENROUTER_API_KEY environment variable.
        Loads local .env variables automatically unless running inside a test framework.
        """
        if "pytest" not in sys.modules:
            try:
                from dotenv import load_dotenv
                load_dotenv()
            except ImportError:
                pass

        self.openrouter_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self.openrouter_key:
            logger.debug("No OPENROUTER_API_KEY configured. EntityExtractor LLM fallback is disabled.")

    def extract(self, text: str) -> Tuple[List[Entity], List[Relation]]:
        """
        Resolves entities using a hybrid approach:
        - First attempts to match known standard patterns using a Regex parser.
        - Falls back to OpenRouter's LLM parser (NVIDIA Nemotron 3 Ultra) if regex doesn't match and API key is set.
        - Returns empty lists if neither can parse the text.
        - Applies canonical naming normalization to all extracted entities and adjusts relationships accordingly.
        """
        # 1. Try regex-based parser
        entities, relations = self._extract_via_regex(text)
        if entities:
            return entities, relations

        # 2. Try OpenRouter LLM-based parser if API key is initialized
        if self.openrouter_key:
            try:
                return self._extract_via_openrouter(text)
            except Exception as e:
                logger.error(f"Error during OpenRouter LLM extraction: {e}")
                return [], []

        # 3. Fallback when API key is missing
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

    def _extract_via_openrouter(self, text: str) -> Tuple[List[Entity], List[Relation]]:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/erdometo/semantic-agent-graph",
            "X-Title": "Semantic Agent Graph"
        }

        # Uses environment variable or falls back to nvidia/nemotron-3-ultra-550b-a55b:free
        model = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")

        system_instruction = (
            "You are an expert system that extracts structured entities and relations from developer "
            "and system logs. Identify systems, errors, configurations, components, users, services, "
            "or IPs, and link them using descriptive relations (e.g. 'CONFIGURED_WITH', 'ENCOUNTERED', "
            "'CONNECTED_FROM', 'CALLED').\n"
            "You must respond ONLY with a JSON object adhering to this schema:\n"
            "{\n"
            "  \"entities\": [\n"
            "    {\"id\": \"string\", \"type\": \"string\", \"name\": \"string\", \"data\": {}}\n"
            "  ],\n"
            "  \"relations\": [\n"
            "    {\"id\": \"string\", \"type\": \"string\", \"source\": \"string\", \"target\": \"string\", \"data\": {}}\n"
            "  ]\n"
            "}"
        )

        prompt_text = (
            f"Analyze the following developer/system log text and extract all semantic entities "
            f"and their relations.\n\n"
            f"Log Text:\n{text}\n\n"
            f"Provide the response as JSON adhering strictly to the schema provided. "
            f"Make sure that any relation's source and target match the ID of an extracted entity."
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt_text}
            ],
            "response_format": {"type": "json_object"}
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        # Retry configuration
        max_retries = 3
        backoff_seconds = 2.0

        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=20) as response:
                    res_body = response.read().decode("utf-8")
                    res_data = json.loads(res_body)
                    
                    choices = res_data.get("choices")
                    if not choices:
                        logger.error(f"OpenRouter returned empty choices: {res_data}")
                        return [], []
                        
                    content = choices[0]["message"]["content"].strip()
                    
                    # Strip markdown code blocks if the model wrapped the JSON (e.g. ```json ... ```)
                    if content.startswith("```"):
                        content = re.sub(r"^```(?:json)?\n?", "", content, flags=re.IGNORECASE)
                        content = re.sub(r"\n?```$", "", content).strip()
                    
                    # Parse response content into ExtractionResponseSchema Pydantic model
                    raw_result = ExtractionResponseSchema.model_validate_json(content)
                    
                    entities = []
                    relations = []
                    id_mapping = {}

                    # Normalize and construct Entity objects
                    for ent in raw_result.entities:
                        normalized_name = normalize_name(ent.name)
                        normalized_type = get_canonical_type(normalized_name, ent.type)
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

            except urllib.error.HTTPError as he:
                if he.code == 429 or he.code >= 500:
                    # Rate limit or server error - retry with backoff
                    if attempt < max_retries - 1:
                        logger.warning(f"OpenRouter returned {he.code} (attempt {attempt+1}/{max_retries}). Retrying in {backoff_seconds}s...")
                        time.sleep(backoff_seconds)
                        backoff_seconds *= 2
                        continue
                logger.error(f"HTTPError contacting OpenRouter API ({he.code}): {he.read().decode('utf-8')}")
                return [], []
            except (http.client.IncompleteRead, urllib.error.URLError) as ce:
                # Connection reset or drop - retry with backoff
                if attempt < max_retries - 1:
                    logger.warning(f"OpenRouter connection error: {ce} (attempt {attempt+1}/{max_retries}). Retrying in {backoff_seconds}s...")
                    time.sleep(backoff_seconds)
                    backoff_seconds *= 2
                    continue
                logger.error(f"Connection error contacting OpenRouter API: {ce}")
                return [], []
            except Exception as e:
                logger.error(f"Failed to communicate with or parse OpenRouter response: {e}")
                return [], []

        return [], []
