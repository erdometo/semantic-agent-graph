import json
import logging
from typing import List, Dict, Any, Optional
from neo4j import GraphDatabase

logger = logging.getLogger(__name__)


class Neo4jMemoryTool:
    """
    Memory retrieval tool for Blooming-ActiveGraph.
    Queries the Neo4j read model to find successful past trajectories (episodes)
    associated with specific entity signatures.
    """

    def __init__(self, uri: str = "bolt://localhost:7687", auth: tuple = ("neo4j", "password")):
        """
        Initializes connection to Neo4j.
        """
        self.driver = GraphDatabase.driver(uri, auth=auth)
        logger.info(f"Initialized Neo4jMemoryTool driver connected to {uri}")

    def close(self) -> None:
        """
        Closes the Neo4j driver connection.
        """
        if self.driver:
            try:
                self.driver.close()
                logger.info("Closed Neo4jMemoryTool driver connection")
            except Exception as e:
                logger.error(f"Failed to close Neo4j Memory Tool driver: {e}", exc_info=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def query_past_trajectories(self, entity_names: List[str]) -> Dict[str, Any]:
        """
        Queries Neo4j to find successful past execution trajectories matching entity names.

        Specifically, it:
        1. Finds past Event nodes that processed target Entities whose names match `entity_names`.
        2. Ensures the parent Run of these events completed successfully (contains 'run.completed' or 'task.success').
        3. Retrieves the path graph of events surrounding the match (up to 2 steps before/after via NEXT).
        4. Retrieves connections from these events to any Entity nodes.
        5. Formats the results into:
           {
               "nodes": [{"id": str, "labels": list[str], "properties": dict}],
               "relationships": [{"type": str, "start": str, "end": str, "properties": dict}]
           }
        """
        if not entity_names:
            return {"nodes": [], "relationships": []}

        # Cypher query to retrieve matching events from successful runs, their surrounding events,
        # and connections to Entities.
        query = """
        MATCH (r:Run)-[:CONTAINS]->(success:Event)
        WHERE success.type IN ["run.completed", "task.success"]
        MATCH (r)-[:CONTAINS]->(e:Event)-[:PROCESSED]->(target:Entity)
        WHERE target.name IN $entity_names
        WITH DISTINCT e
        MATCH (e)-[:NEXT*0..2]-(surr:Event)
        WITH collect(DISTINCT surr) AS surr_list
        UNWIND surr_list AS surr
        OPTIONAL MATCH (surr)-[next_rel:NEXT]->(other:Event)
        WHERE other IN surr_list
        OPTIONAL MATCH (surr)-[proc_rel:PROCESSED]->(ent:Entity)
        RETURN surr, next_rel, proc_rel, ent
        """

        nodes_map = {}
        rels_map = {}

        def _get_node_id(node) -> str:
            if "Event" in node.labels:
                return node.get("id") or str(node.id)
            if "Run" in node.labels:
                return node.get("run_id") or str(node.id)
            if "Entity" in node.labels:
                return node.get("id") or node.get("name") or str(node.id)
            return node.get("id") or node.get("run_id") or str(node.id)

        def _clean_properties(properties: dict) -> dict:
            cleaned = {}
            for k, v in properties.items():
                if isinstance(v, str):
                    # Try to deserialize JSON payload or data structures
                    if k in ("payload", "data"):
                        try:
                            cleaned[k] = json.loads(v)
                            continue
                        except Exception:
                            pass
                cleaned[k] = v
            return cleaned

        try:
            with self.driver.session() as session:
                result = session.run(query, entity_names=entity_names)
                for record in result:
                    surr = record["surr"]
                    next_rel = record["next_rel"]
                    proc_rel = record["proc_rel"]
                    ent = record["ent"]

                    if surr:
                        s_id = _get_node_id(surr)
                        if s_id not in nodes_map:
                            nodes_map[s_id] = {
                                "id": s_id,
                                "labels": list(surr.labels),
                                "properties": _clean_properties(dict(surr))
                            }

                    if ent:
                        e_id = _get_node_id(ent)
                        if e_id not in nodes_map:
                            nodes_map[e_id] = {
                                "id": e_id,
                                "labels": list(ent.labels),
                                "properties": _clean_properties(dict(ent))
                            }

                    if next_rel:
                        start_id = _get_node_id(next_rel.start_node)
                        end_id = _get_node_id(next_rel.end_node)
                        rel_key = (next_rel.type, start_id, end_id)
                        if rel_key not in rels_map:
                            rels_map[rel_key] = {
                                "type": next_rel.type,
                                "start": start_id,
                                "end": end_id,
                                "properties": _clean_properties(dict(next_rel))
                            }

                    if proc_rel:
                        start_id = _get_node_id(proc_rel.start_node)
                        end_id = _get_node_id(proc_rel.end_node)
                        rel_key = (proc_rel.type, start_id, end_id)
                        if rel_key not in rels_map:
                            rels_map[rel_key] = {
                                "type": proc_rel.type,
                                "start": start_id,
                                "end": end_id,
                                "properties": _clean_properties(dict(proc_rel))
                            }
        except Exception as err:
            logger.error(f"Error querying past trajectories: {err}", exc_info=True)
            raise

        return {
            "nodes": list(nodes_map.values()),
            "relationships": list(rels_map.values())
        }
