import uuid
import datetime
import logging
import hashlib
import json
import inspect
import contextlib
import contextvars
import re
from typing import Callable, Optional, List, Dict, Any

from semantic_agent_graph.models import Event, Run
from semantic_agent_graph.store import SQLiteEventStore

logger = logging.getLogger(__name__)

# A global registry for behaviors registered via the global @behavior decorator
_GLOBAL_BEHAVIORS: List[Dict[str, Any]] = []


def behavior(on_events: List[str], where: Optional[Dict[str, Any]] = None) -> Callable:
    """
    Global decorator to register standard behaviors.
    These behaviors will be automatically registered to any ReactiveRuntime when it is initialized.
    """
    def decorator(fn: Callable) -> Callable:
        _GLOBAL_BEHAVIORS.append({
            "name": fn.__name__,
            "on_events": on_events,
            "fn": fn,
            "where": where or {}
        })
        return fn
    return decorator


class Neo4jProjection:
    """
    Neo4j projection engine for ActiveGraph.
    Synchronizes events, runs, entities, and relations from the log into a Neo4j database.
    """
    def __init__(self, uri: Optional[str] = None, auth: Optional[tuple] = None, driver: Any = None):
        self.driver = driver
        self.uri = uri
        self.auth = auth
        if self.driver is None and uri is not None:
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(uri, auth=auth)

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()

    def apply_event(self, event: Event) -> None:
        """
        Projects an Event object to Neo4j.
        Constructs the Episodic Trajectory Layer and dynamically applies Semantic Layer updates.
        """
        if self.driver is None:
            return

        with self.driver.session() as session:
            # 1. Project the Event node and ensure the Run node exists
            session.run(
                """
                MERGE (r:Run {run_id: $run_id})
                MERGE (e:Event {id: $id})
                SET e.seq = $seq,
                    e.type = $type,
                    e.actor = $actor,
                    e.payload = $payload_json,
                    e.frame_id = $frame_id,
                    e.caused_by = $caused_by,
                    e.timestamp = $timestamp,
                    e.run_id = $run_id
                MERGE (r)-[:CONTAINS]->(e)
                """,
                run_id=event.run_id,
                id=event.id,
                seq=event.seq,
                type=event.type,
                actor=event.actor,
                payload_json=json.dumps(event.payload),
                frame_id=event.frame_id,
                caused_by=event.caused_by,
                timestamp=event.timestamp
            )

            # 2. Link chronologically [:NEXT]
            session.run(
                """
                MATCH (e:Event {id: $id})
                MATCH (r:Run {run_id: $run_id})
                OPTIONAL MATCH (prev:Event)
                WHERE prev.run_id = $run_id AND prev.id <> $id AND prev.seq < $seq
                WITH e, r, prev
                ORDER BY prev.seq DESC
                LIMIT 1
                FOREACH (p IN CASE WHEN prev IS NOT NULL THEN [prev] ELSE [] END |
                    MERGE (p)-[:NEXT]->(e)
                )
                FOREACH (dummy IN CASE WHEN prev IS NULL THEN [1] ELSE [] END |
                    MERGE (r)-[:NEXT]->(e)
                )
                """,
                id=event.id,
                run_id=event.run_id,
                seq=event.seq
            )

            # 3. Link caused_by if present
            if event.caused_by:
                session.run(
                    """
                    MATCH (e:Event {id: $id})
                    MATCH (cause:Event {id: $caused_by})
                    MERGE (e)-[:CAUSED_BY]->(cause)
                    """,
                    id=event.id,
                    caused_by=event.caused_by
                )

            # 4. Handle Semantic / Domain Events
            # entity.created / object.created
            if event.type in ("object.created", "entity.created"):
                obj_id = event.payload.get("id")
                if obj_id:
                    obj_type = event.payload.get("type", "Entity")
                    name = event.payload.get("name", "")
                    data = event.payload.get("data", {})
                    safe_type = re.sub(r'[^a-zA-Z0-9_]', '', obj_type)
                    
                    session.run(
                        f"""
                        MERGE (n:Entity {{id: $id, run_id: $run_id}})
                        SET n.name = $name, n.data = $data_json, n.type = $type
                        WITH n
                        MATCH (e:Event {{id: $event_id}})
                        MERGE (e)-[:PROCESSED]->(n)
                        WITH n
                        SET n:{safe_type}
                        """,
                        id=obj_id,
                        run_id=event.run_id,
                        name=name,
                        data_json=json.dumps(data),
                        type=obj_type,
                        event_id=event.id
                    )
            
            # entity.patched / object.patched
            elif event.type in ("object.patched", "entity.patched"):
                obj_id = event.payload.get("id")
                if obj_id:
                    data = event.payload.get("data", {})
                    # Retrieve the existing data and merge it in Python
                    result = session.run(
                        """
                        MATCH (n:Entity {id: $id, run_id: $run_id})
                        RETURN n.data AS data
                        """,
                        id=obj_id,
                        run_id=event.run_id
                    )
                    record = result.single()
                    existing_data = {}
                    if record and record["data"]:
                        try:
                            existing_data = json.loads(record["data"])
                        except Exception:
                            pass
                    
                    # Merge dictionaries
                    existing_data.update(data)
                    
                    session.run(
                        """
                        MATCH (n:Entity {id: $id, run_id: $run_id})
                        SET n.data = $data_json
                        """,
                        id=obj_id,
                        run_id=event.run_id,
                        data_json=json.dumps(existing_data)
                    )

            # entity.removed / object.removed
            elif event.type in ("object.removed", "entity.removed"):
                obj_id = event.payload.get("id")
                if obj_id:
                    session.run(
                        """
                        MATCH (n:Entity {id: $id, run_id: $run_id})
                        DETACH DELETE n
                        """,
                        id=obj_id,
                        run_id=event.run_id
                    )

            # relation.created
            elif event.type == "relation.created":
                rel_id = event.payload.get("id")
                rel_type = event.payload.get("type", "RELATION")
                source_id = event.payload.get("source")
                target_id = event.payload.get("target")
                data = event.payload.get("data", {})
                
                if source_id and target_id:
                    safe_rel_type = re.sub(r'[^a-zA-Z0-9_]', '', rel_type)
                    session.run(
                        f"""
                        MATCH (source {{id: $source_id, run_id: $run_id}})
                        MATCH (target {{id: $target_id, run_id: $run_id}})
                        MERGE (source)-[r:{safe_rel_type} {{id: $rel_id, run_id: $run_id}}]->(target)
                        SET r.data = $data_json
                        """,
                        source_id=source_id,
                        target_id=target_id,
                        rel_id=rel_id,
                        run_id=event.run_id,
                        data_json=json.dumps(data)
                    )

    def fork_run(
        self,
        parent_run_id: str,
        new_run: Run,
        forked_at_event_id: str,
        copied_events: List[Event]
    ) -> None:
        """
        Projects a run fork into Neo4j.
        Creates the new Run node, links it to the parent fork point event,
        and projects all copied events under the new run_id.
        """
        if self.driver is None:
            return

        with self.driver.session() as session:
            # 1. Create the new Run node
            session.run(
                """
                MERGE (r:Run {run_id: $run_id})
                SET r.parent_run_id = $parent_run_id,
                    r.forked_at_event_id = $forked_at_event_id,
                    r.label = $label,
                    r.created_at = $created_at,
                    r.goal = $goal,
                    r.frame_id = $frame_id
                """,
                run_id=new_run.run_id,
                parent_run_id=parent_run_id,
                forked_at_event_id=forked_at_event_id,
                label=new_run.label,
                created_at=new_run.created_at,
                goal=new_run.goal,
                frame_id=new_run.frame_id
            )

            # 2. Link the new Run node to the parent fork point event node
            session.run(
                """
                MATCH (r:Run {run_id: $run_id})
                MATCH (e:Event {id: $forked_at_event_id, run_id: $parent_run_id})
                MERGE (r)-[:FORKED_FROM]->(e)
                """,
                run_id=new_run.run_id,
                forked_at_event_id=forked_at_event_id,
                parent_run_id=parent_run_id
            )

        # 3. Project all copied events with their new run_id
        for event in copied_events:
            self.apply_event(event)


class ReactiveRuntime:
    """
    Reactive loop runtime for Blooming-ActiveGraph.
    Manages event emission, behavior registration, dispatching, LLM caching, and branching/forking.
    """
    _active_run_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("active_run_id", default=None)

    def __init__(self, store: SQLiteEventStore, projection: Optional[Neo4jProjection] = None):
        self.store = store
        self.projection = projection
        self.behaviors: List[Dict[str, Any]] = []
        self.event_queue: List[Event] = []

        # Automatically register globally decorated behaviors
        for b in _GLOBAL_BEHAVIORS:
            self.register_behavior(b["name"], b["on_events"], b["fn"], b["where"])

    @property
    def active_run_id(self) -> Optional[str]:
        """Gets the active run_id from context."""
        return self._active_run_id.get()

    @active_run_id.setter
    def active_run_id(self, value: Optional[str]) -> None:
        """Sets the active run_id in context."""
        self._active_run_id.set(value)

    @contextlib.contextmanager
    def active_run(self, run_id: str):
        """Context manager to scope operations to a specific run_id."""
        token = self._active_run_id.set(run_id)
        try:
            yield
        finally:
            self._active_run_id.reset(token)

    def register_behavior(self, name: str, on_events: List[str], fn: Callable, where: Optional[Dict[str, Any]] = None) -> None:
        """Registers a behavior handler on this runtime."""
        self.behaviors.append({
            "name": name,
            "on_events": on_events,
            "on": on_events,  # alias
            "fn": fn,
            "where": where or {}
        })

    def behavior(self, on_events: List[str], where: Optional[Dict[str, Any]] = None) -> Callable:
        """Instance decorator to register standard behaviors."""
        def decorator(fn: Callable) -> Callable:
            self.register_behavior(fn.__name__, on_events, fn, where)
            return fn
        return decorator

    def emit(
        self,
        event_type: str,
        payload: Dict[str, Any],
        actor: Optional[str] = None,
        caused_by: Optional[str] = None,
        frame_id: Optional[str] = None
    ) -> Event:
        """
        Creates an Event object with a generated UUID, current ISO timestamp, and run_id from
        the active context, appends it to the SQLite store, projects to Neo4j if available, and queues it.
        """
        run_id = self.active_run_id
        if not run_id:
            raise RuntimeError("No active run context. Cannot emit event.")

        event_id = str(uuid.uuid4())
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        event = Event(
            id=event_id,
            type=event_type,
            actor=actor,
            payload=payload,
            frame_id=frame_id,
            caused_by=caused_by,
            timestamp=timestamp,
            run_id=run_id
        )

        # Append to SQLite store (sets seq in-place)
        self.store.append_event(event)

        # Project to Neo4j if available
        if self.projection is not None:
            try:
                self.projection.apply_event(event)
            except Exception as e:
                logger.error(f"Failed to project event {event_id} to Neo4j: {e}", exc_info=True)

        # Queue for dispatch loop
        self.event_queue.append(event)

        return event

    def _match_where(self, payload: Dict[str, Any], where: Dict[str, Any]) -> bool:
        """Recursively matches event payload properties defined in where."""
        if not where:
            return True
        for key, val in where.items():
            if key not in payload:
                return False
            # Support nested dict comparison
            if isinstance(val, dict) and isinstance(payload[key], dict):
                if not self._match_where(payload[key], val):
                    return False
            elif payload[key] != val:
                return False
        return True

    def dispatch_loop(self) -> None:
        """Pops events from the queue, matches them against behaviors, and runs the behavior."""
        while self.event_queue:
            event = self.event_queue.pop(0)
            for behavior_item in self.behaviors:
                on_events = behavior_item.get("on_events") or []
                where = behavior_item.get("where") or {}

                # Check type matching
                type_match = False
                if "*" in on_events or event.type in on_events:
                    type_match = True

                if type_match and self._match_where(event.payload, where):
                    fn = behavior_item["fn"]
                    try:
                        sig = inspect.signature(fn)
                        params = list(sig.parameters.values())

                        # Execute the behavior under the event's run context
                        with self.active_run(event.run_id):
                            if len(params) >= 2:
                                fn(self, event)
                            elif len(params) == 1:
                                fn(event)
                            else:
                                fn()
                    except Exception as e:
                        logger.error(
                            f"Error executing behavior '{behavior_item.get('name')}' for event {event.id}: {e}",
                            exc_info=True
                        )

    def llm_call(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        settings: Dict[str, Any],
        call_fn: Callable[[], str]
    ) -> str:
        """
        Implements the Caching & Determinism contract.
        Calculates prompt hash, emits llm.requested, checks cache, and returns/saves output.
        """
        run_id = self.active_run_id
        if not run_id:
            raise RuntimeError("No active run context. Cannot perform LLM call.")

        # Calculate prompt hash
        prompt_payload = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "model": model,
            "settings": settings
        }
        serialized = json.dumps(prompt_payload, sort_keys=True)
        prompt_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

        # Emit llm.requested
        self.emit(
            "llm.requested",
            payload={
                "prompt_hash": prompt_hash,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model": model,
                "settings": settings
            },
            actor="system"
        )

        # Check for cached response in active run's event log
        events = self.store.get_events(run_id)
        cached_response = None
        for event in events:
            if event.type == "llm.responded":
                if event.payload.get("prompt_hash") == prompt_hash:
                    cached_response = event.payload.get("response")
                    break

        if cached_response is not None:
            # Emit llm.responded with cache indicator
            self.emit(
                "llm.responded",
                payload={
                    "prompt_hash": prompt_hash,
                    "response": cached_response,
                    "cached": True,
                    "cache_hit": True
                },
                actor="system"
            )
            return cached_response

        # Execute fresh LLM call
        output = call_fn()

        # Emit llm.responded with output
        self.emit(
            "llm.responded",
            payload={
                "prompt_hash": prompt_hash,
                "response": output,
                "cached": False,
                "cache_hit": False
            },
            actor="system"
        )

        return output

    def fork_run(self, parent_run_id: str, new_run_id: str, forked_at_event_id: str) -> Run:
        """
        Forks the run in the SQLite store. If projection is set, projects the new run metadata
        and copied events to Neo4j, making sure to link the new Run to the parent fork point event.
        """
        # Fork in SQLite store
        new_run = self.store.fork_run(
            parent_run_id=parent_run_id,
            new_run_id=new_run_id,
            forked_at_event_id=forked_at_event_id
        )

        # Get copied events from the SQLite store
        copied_events = self.store.get_events(new_run_id)

        # Sync to Neo4j if projection is set
        if self.projection is not None:
            try:
                self.projection.fork_run(
                    parent_run_id=parent_run_id,
                    new_run=new_run,
                    forked_at_event_id=forked_at_event_id,
                    copied_events=copied_events
                )
            except Exception as e:
                logger.error(
                    f"Failed to project fork from {parent_run_id} to {new_run_id} in Neo4j: {e}",
                    exc_info=True
                )

        return new_run
