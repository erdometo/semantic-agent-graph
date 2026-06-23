import json
import logging
import re
from typing import Any, Dict, Optional

from neo4j import GraphDatabase
from semantic_agent_graph.models import Event

logger = logging.getLogger(__name__)


class Neo4jProjection:
    """
    Neo4j projection layer for Blooming-ActiveGraph.
    Projects events into a Neo4j database to build and maintain the read model graph state.
    """

    def __init__(self, uri: str = "bolt://localhost:7687", auth: tuple = ("neo4j", "password")):
        """
        Initializes connection to Neo4j.
        """
        self.driver = GraphDatabase.driver(uri, auth=auth)
        logger.info(f"Initialized Neo4jProjection driver connected to {uri}")

    def close(self) -> None:
        """
        Closes driver connection.
        """
        try:
            self.driver.close()
            logger.info("Closed Neo4j driver connection")
        except Exception as e:
            logger.error(f"Failed to close Neo4j driver: {e}", exc_info=True)

    def clear_db(self) -> None:
        """
        Deletes all nodes and relationships in Neo4j.
        Useful for starting clean in tests.
        """
        try:
            with self.driver.session() as session:
                session.run("MATCH (n) DETACH DELETE n")
            logger.info("Cleared Neo4j database (deleted all nodes and relationships)")
        except Exception as e:
            logger.error(f"Failed to clear Neo4j database: {e}", exc_info=True)
            raise

    def apply_event(self, event: Event) -> None:
        """
        Matches event types and projects them into Neo4j.
        """
        try:
            with self.driver.session() as session:
                session.execute_write(self._apply_event_tx, event)
        except Exception as e:
            logger.error(f"Failed to project event {event.id} to Neo4j: {e}", exc_info=True)
            raise

    def _normalize_name(self, name: str) -> str:
        """
        Helper method to map entity names to their canonical forms.
        - 'postgresql', 'postgres', 'pg' -> 'Postgres'
        - 'port 5432', '5432' -> 'Port 5432'
        - 'timeouterror', 'timeout' -> 'TimeoutError'
        """
        if not name:
            return ""
        name_clean = str(name).strip().lower()
        mapping = {
            "postgresql": "Postgres",
            "postgres": "Postgres",
            "pg": "Postgres",
            "port 5432": "Port 5432",
            "5432": "Port 5432",
            "timeouterror": "TimeoutError",
            "timeout": "TimeoutError"
        }
        return mapping.get(name_clean, str(name).strip())

    def _find_entity(self, tx, run_id: str, entity_id: str, entity_name: Optional[str] = None) -> Optional[str]:
        """
        Helper method to locate the canonical name of a global Entity node.
        Looks up the entity by canonical name, entity ID, or via the processed events in the same run.
        """
        # 1. Try to find Entity with name = normalized_name if entity_name is given
        if entity_name:
            norm_name = self._normalize_name(entity_name)
            result = tx.run("MATCH (ent:Entity {name: $name}) RETURN ent.name AS name", name=norm_name)
            row = result.single()
            if row:
                return row["name"]

        # 2. Try to find Entity with id = entity_id
        if entity_id:
            result = tx.run("MATCH (ent:Entity {id: $entity_id}) RETURN ent.name AS name", entity_id=entity_id)
            row = result.single()
            if row:
                return row["name"]

        # 3. Fallback: search via processed events in the same run
        if run_id and entity_id:
            result = tx.run(
                "MATCH (e:Event {run_id: $run_id})-[:PROCESSED]->(ent:Entity) RETURN ent.name AS name, e.payload AS payload",
                run_id=run_id
            )
            for record in result:
                try:
                    payload_dict = json.loads(record["payload"])
                    if payload_dict.get("id") == entity_id:
                        return record["name"]
                except Exception:
                    continue

        # 4. Fallback 2: check if entity_id itself maps to a canonical name
        if entity_id:
            norm_id = self._normalize_name(entity_id)
            result = tx.run("MATCH (ent:Entity {name: $name}) RETURN ent.name AS name", name=norm_id)
            row = result.single()
            if row:
                return row["name"]

        return None

    def _apply_event_tx(self, tx, event: Event) -> None:
        """
        Performs the database writes within a Neo4j transaction.
        """
        payload_str = json.dumps(event.payload)

        # 1. Project base Event node and connect to Run node
        tx.run(
            """
            MERGE (r:Run {run_id: $run_id})
            MERGE (e:Event {id: $id, run_id: $run_id})
            SET e.seq = $seq,
                e.type = $type,
                e.actor = $actor,
                e.payload = $payload,
                e.frame_id = $frame_id,
                e.caused_by = $caused_by,
                e.timestamp = $timestamp
            MERGE (r)-[:CONTAINS]->(e)
            """,
            run_id=event.run_id,
            id=event.id,
            seq=event.seq,
            type=event.type,
            actor=event.actor,
            payload=payload_str,
            frame_id=event.frame_id,
            caused_by=event.caused_by,
            timestamp=event.timestamp
        )

        # 2. Draw CAUSED_BY edge if caused_by is present
        if event.caused_by:
            tx.run(
                """
                MATCH (e:Event {id: $id, run_id: $run_id})
                MERGE (cause:Event {id: $caused_by, run_id: $run_id})
                MERGE (e)-[:CAUSED_BY]->(cause)
                """,
                id=event.id,
                run_id=event.run_id,
                caused_by=event.caused_by
            )

        # 3. Maintain chronological sequence via NEXT edge
        if event.seq is not None:
            prev_seq = event.seq - 1
            tx.run(
                """
                MATCH (prev:Event {seq: $prev_seq})
                MATCH (curr:Event {id: $id, run_id: $run_id})
                MERGE (prev)-[:NEXT]->(curr)
                """,
                prev_seq=prev_seq,
                id=event.id,
                run_id=event.run_id
            )

        # 4. Project specific event types
        if event.type == "object.created":
            raw_name = event.payload.get("name")
            if not raw_name:
                raw_name = event.payload.get("id", "Unknown")

            normalized_name = self._normalize_name(raw_name)
            entity_id = event.payload.get("id")
            entity_type = event.payload.get("type", "Entity")
            entity_data = event.payload.get("data", {})
            entity_data_str = json.dumps(entity_data)

            tx.run(
                """
                MERGE (ent:Entity {name: $normalized_name})
                ON CREATE SET ent.id = $entity_id, ent.type = $type, ent.data = $data
                ON MATCH SET ent.id = $entity_id, ent.type = $type, ent.data = $data
                WITH ent
                MATCH (e:Event {id: $event_id, run_id: $run_id})
                MERGE (e)-[:PROCESSED]->(ent)
                """,
                normalized_name=normalized_name,
                entity_id=entity_id,
                type=entity_type,
                data=entity_data_str,
                event_id=event.id,
                run_id=event.run_id
            )

        elif event.type == "object.patched":
            entity_id = event.payload.get("id")
            entity_name = event.payload.get("name")
            patch_data = event.payload.get("data", {})

            canonical_name = self._find_entity(tx, event.run_id, entity_id, entity_name)
            if canonical_name:
                # Read current data and update
                res = tx.run("MATCH (ent:Entity {name: $name}) RETURN ent.data AS data", name=canonical_name)
                row = res.single()
                current_data = {}
                if row and row["data"]:
                    try:
                        current_data = json.loads(row["data"])
                    except Exception:
                        pass

                current_data.update(patch_data)

                tx.run(
                    "MATCH (ent:Entity {name: $name}) SET ent.data = $data",
                    name=canonical_name,
                    data=json.dumps(current_data)
                )
            else:
                # Fallback: create the Entity if it didn't exist
                fallback_name = self._normalize_name(entity_name or entity_id or "Unknown")
                tx.run(
                    """
                    MERGE (ent:Entity {name: $name})
                    ON CREATE SET ent.id = $id, ent.data = $data
                    ON MATCH SET ent.data = $data
                    """,
                    name=fallback_name,
                    id=entity_id,
                    data=json.dumps(patch_data)
                )

        elif event.type == "relation.created":
            source_id = event.payload.get("source")
            target_id = event.payload.get("target")
            rel_type = event.payload.get("type", "RELATED_TO").upper().strip()
            rel_data = event.payload.get("data", {})

            # Clean and sanitize the relationship type
            clean_rel_type = re.sub(r'[^A-Za-z0-9_]', '_', rel_type)
            if not clean_rel_type:
                clean_rel_type = "RELATED_TO"

            source_name = self._find_entity(tx, event.run_id, source_id)
            if not source_name:
                source_name = self._normalize_name(source_id)

            target_name = self._find_entity(tx, event.run_id, target_id)
            if not target_name:
                target_name = self._normalize_name(target_id)

            # Ensure both entities exist before drawing relation
            tx.run("MERGE (source:Entity {name: $name})", name=source_name)
            tx.run("MERGE (target:Entity {name: $name})", name=target_name)

            query = f"""
            MATCH (source:Entity {{name: $source_name}})
            MATCH (target:Entity {{name: $target_name}})
            MERGE (source)-[r:{clean_rel_type}]->(target)
            SET r.data = $data
            """
            tx.run(
                query,
                source_name=source_name,
                target_name=target_name,
                data=json.dumps(rel_data)
            )

        elif event.type == "run.forked":
            new_run_id = event.payload.get("new_run_id") or event.payload.get("child_run_id")
            parent_run_id = event.payload.get("parent_run_id")
            parent_event_id = event.payload.get("forked_at_event_id") or event.payload.get("parent_event_id") or event.caused_by

            if not new_run_id and parent_run_id:
                new_run_id = event.run_id

            if not parent_event_id and parent_run_id:
                # Query Neo4j for the latest event of the parent run
                res = tx.run(
                    """
                    MATCH (r:Run {run_id: $parent_run_id})-[:CONTAINS]->(e:Event)
                    RETURN e.id AS id
                    ORDER BY e.seq DESC LIMIT 1
                    """,
                    parent_run_id=parent_run_id
                )
                row = res.single()
                if row:
                    parent_event_id = row["id"]

            if new_run_id:
                # Project/merge the new Run node
                tx.run("MERGE (r:Run {run_id: $new_run_id})", new_run_id=new_run_id)

                if parent_event_id:
                    # Draw a [:FORKED_FROM] edge from the new Run node to the parent Event node
                    tx.run(
                        """
                        MATCH (r:Run {run_id: $new_run_id})
                        MERGE (parent_ev:Event {id: $parent_event_id})
                        MERGE (r)-[:FORKED_FROM]->(parent_ev)
                        """,
                        new_run_id=new_run_id,
                        parent_event_id=parent_event_id
                    )
