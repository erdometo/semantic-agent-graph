# Semantic Agent Graph Source Reference

This sub-package contains the implementation modules for the **Semantic Agent Graph (SAG)**. It provides components for event logging, reactive runtimes, entity extraction, and memory graph query tools.

---

## 1. Module Reference

*   **[models.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/semantic_agent_graph/models.py):** Contains the Pydantic schemas for the key records:
    *   `Event`: Defines log entries (type, payload, actor, caused_by, timestamp, sequence).
    *   `Run`: Defines execution metadata (run ID, goal, parent run ID, fork point).
    *   `Entity`: Represents normalized domain entities (systems, errors, configurations).
    *   `Relation`: Represents typed semantic connections between entities.
*   **[store.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/semantic_agent_graph/store.py):** Implements `SQLiteEventStore`, a thread-safe append-only event log managing concurrency and parent fork copies.
*   **[runtime.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/semantic_agent_graph/runtime.py):** Implements `ReactiveRuntime`, which manages the main event queue, contextvars-based run scoping, behavior routing, caching, and run forks.
*   **[projection.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/semantic_agent_graph/projection.py):** Implements `Neo4jProjection`, translating events to Neo4j nodes and edges in real time.
*   **[extraction.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/semantic_agent_graph/extraction.py):** Implements `EntityExtractor`, combining regex log match rules with Gemini structured output fallbacks.
*   **[memory.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/semantic_agent_graph/memory.py):** Implements `Neo4jMemoryTool`, executing graph query patterns to retrieve successful path sub-graphs.
*   **[parser_swe.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/semantic_agent_graph/parser_swe.py):** Implements pipeline functions to parse standard SWE-agent trajectory files.

---

## 2. SQLite Database Schema

The `SQLiteEventStore` manages two tables:

### A. The `runs` Table
Stores high-level goal and lineage tracking metadata.
```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    parent_run_id TEXT,
    forked_at_event_id TEXT,
    label TEXT,
    created_at TEXT NOT NULL,
    goal TEXT,
    frame_id TEXT
);
```

### B. The `events` Table
An append-only log of all sequential execution events.
```sql
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    type TEXT NOT NULL,
    actor TEXT,
    payload TEXT NOT NULL, -- JSON string
    frame_id TEXT,
    caused_by TEXT,
    timestamp TEXT NOT NULL,
    run_id TEXT NOT NULL,
    UNIQUE(id, run_id)
);
```

---

## 3. Standard Event Types Reference

The runtime is built around a standardized schema of event types:

*   `run.created`: Emitted when a new agent run is initialized.
*   `agent.step`: Emitted during each turn of the agent loop. Holds thoughts, actions, and observations.
*   `object.created` / `entity.created`: Emitted when the extraction module identifies a new normalized system, error, or configuration.
*   `object.patched` / `entity.patched`: Emitted when properties of an existing entity are updated.
*   `relation.created`: Emitted when a relationship between two entities is established.
*   `llm.requested`: Emitted right before calling an LLM provider. Includes prompt hash and configuration.
*   `llm.responded`: Emitted after an LLM provider completes. Contains response payload and a `cached` boolean indicator.
*   `run.completed` / `task.success`: Emitted when the run successfully finishes.
*   `run.failed` / `task.failure`: Emitted when the run terminates in a failure state. This terminal outcome is indexed in Neo4j to fuel the **Predictive Dead-End Detection** engine.

---

## 4. Extension Guide: Integrating Custom Agent Loops

You can integrate any custom agent loop (such as a LangChain agent, BabyAGI runner, or AutoGPT loop) with SAG by wrapping its inputs and outputs in our event system.

Here is a template demonstrating how to connect a custom agent:

```python
import uuid
import datetime
from semantic_agent_graph import SQLiteEventStore, ReactiveRuntime, Run, EntityExtractor

def run_custom_agent(task_goal: str):
    # 1. Setup storage & runtime
    store = SQLiteEventStore("runs.db")
    runtime = ReactiveRuntime(store=store)
    extractor = EntityExtractor()
    
    run_id = f"custom_run_{uuid.uuid4().hex[:8]}"
    
    # 2. Register run metadata
    run = Run(
        run_id=run_id,
        created_at=datetime.datetime.utcnow().isoformat() + "Z",
        goal=task_goal
    )
    store.create_run(run)
    
    # 3. Define the agent execution inside the run context
    with runtime.active_run(run_id):
        # Emit initial step
        step_1 = runtime.emit(
            "agent.step", 
            payload={"thought": "I need to inspect the settings.", "action": "read settings.py"},
            actor="agent"
        )
        
        # Suppose a tool returns output containing an error
        tool_output = "FileNotFoundError: settings.py not found at root"
        
        # Analyze output and extract entities
        entities, relations = extractor.extract(tool_output)
        
        # Emit extracted entities as sub-events
        for entity in entities:
            runtime.emit(
                "object.created",
                payload={"id": entity.id, "type": entity.type, "name": entity.name},
                caused_by=step_1.id,
                actor="extractor"
            )
            
        # Emit final success or failure
        runtime.emit(
            "run.failed",
            payload={"status": "failed", "reason": "settings.py missing"},
            caused_by=step_1.id,
            actor="system"
        )
        
        # Run loop to trigger any registered behavior handlers
        runtime.dispatch_loop()

if __name__ == "__main__":
    run_custom_agent("Verify settings integrity")
```
