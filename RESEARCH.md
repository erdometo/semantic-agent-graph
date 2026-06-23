# Semantic Agent Graph (SAG) Research Guide

This guide is designed for researchers looking to replicate our benchmarks, perform evaluations, scale the SWE-agent trajectory ingestion pipeline, and build upon our episodic-semantic memory framework.

---

## 1. Architectural Comparison: Nakajima's ActiveGraph vs. SAG

To understand the core contributions of **Semantic Agent Graph (SAG)**, it is essential to contrast it with the foundational architecture proposed by Yohei Nakajima.

| Architectural Dimension | Yohei Nakajima's ActiveGraph (*"The Log is the Agent"*) | Semantic Agent Graph (SAG) [Ours] |
| :--- | :--- | :--- |
| **Primary State Model** | Event log acts as the singular source of truth; state is a projection. | Dual-layer hybrid (CQRS): SQLite write-buffer log + Neo4j query graph. |
| **Memory Representation** | Chronological and structural lineages of events in isolated run views. | **Episodic-Semantic Blooming**: Events are linked to a global semantic relation network. |
| **Cross-Run Synthesis** | Multi-agent workflows communicate via events; runs are separate. | Global entities (e.g. exceptions, packages, files) bridge separate run logs. |
| **Latency Mitigation** | Sequential processing; network hops on every event write/read. | Local SQLite append buffer (**1020x speedup**) + background projection sync. |
| **Branching & Forking** | Creates logical branches by replaying event logs from the fork point. | **Cache Contract Branching**: Hashed LLM logs reduce replay API costs to zero. |
| **Retrieval Mechanism** | Replaying event lineages to establish agent state. | **Sub-Graph Path Retrieval**: Querying successful past solutions by entity signature. |

---

## 2. SWE-Agent Trajectory Mapping Schema

We use SWE-agent trajectories (specifically raw `.traj` interaction logs) to evaluate SAG memory performance. The ingestion pipeline translates the raw model turns into our structured graph nodes and relations.

### Schema Mapping Flow
1.  **Run Node:** Created when a task trajectory begins. In Neo4j: `(r:Run {run_id: $run_id, goal: $goal})`.
2.  **Agent Turn:** Each step in the trajectory array is mapped to a sequential event of type `agent.step`. It is chronological: `(e_prev)-[:NEXT]->(e_curr)` and causal: `(e_curr)-[:CAUSED_BY]->(e_prev)`.
3.  **Semantic Entities:** During each turn, the `EntityExtractor` parses the `action` and `observation` text (using regular expressions or the Gemini structured model) to isolate variables, files, errors, and systems.
    -   *Example action:* `edit django/db/models/query.py` $\rightarrow$ Extracts `Entity` of type `File` with name `query.py`.
    -   *Example observation:* `AttributeError: 'NoneType' object has no attribute 'tzinfo'` $\rightarrow$ Extracts `Entity` of type `Error` with name `AttributeError`.
    -   *Episodic-Semantic link:* The event is linked to these entities via `(e:Event)-[:PROCESSED]->(ent:Entity)`.
4.  **Semantic Relations:** The extractor uncovers relationships between these entities and projects them to Neo4j.
    -   *Example relation:* `(query.py)-[:RAISED]->(AttributeError)` which is projected as `(src)-[r:RAISED]->(tgt)`.

---

## 3. Large-Scale Dataset Integration: `nebius/SWE-agent-trajectories`

For comprehensive benchmarks, we recommend using the **nebius/SWE-agent-trajectories** dataset hosted on Hugging Face. This dataset contains over 80,000 trajectories of agents attempting to solve SWE-bench software engineering tasks.

### A. Downloading the Parquet Files
The Hugging Face dataset is stored in `.parquet` tables. You can load and process it programmatically using the Hugging Face `datasets` and `pandas` libraries:

```python
import json
from datasets import load_dataset

# 1. Load the dataset from Hugging Face
print("Downloading SWE-agent trajectories from Hugging Face...")
dataset = load_dataset("nebius/SWE-agent-trajectories")

# 2. Extract a specific split (e.g., train)
train_data = dataset['train']
print(f"Loaded {len(train_data)} trajectories.")

# 3. Convert a Parquet row into a standard .traj JSON format
first_row = train_data[0]
instance_id = first_row['instance_id']
raw_history = first_row['history'] # Contains steps, thoughts, actions, observations

# Reconstruct the expected JSON structure
trajectory_json = {
    "trajectory": []
}

# Map the turns into standard turns:
for turn in raw_history:
    trajectory_json["trajectory"].append({
        "thought": turn.get("thought", ""),
        "action": turn.get("action", ""),
        "observation": turn.get("observation", "")
    })

# Save locally to be read by parser_swe.py
output_file = f"trajectories_{instance_id}.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(trajectory_json, f, indent=2)
print(f"Saved trajectory for {instance_id} to {output_file}")
```

### B. Bulk Ingestion Script
To ingest a batch of downloaded files into the SQLite store and the local Neo4j database:

```python
import os
import glob
from semantic_agent_graph import SQLiteEventStore, Neo4jProjection, EntityExtractor, ingest_swe_trajectory

def bulk_ingest(directory_path: str):
    store = SQLiteEventStore("research_runs.db")
    
    projection = None
    try:
        projection = Neo4jProjection()
        print("Connected to Neo4j database.")
    except Exception:
        print("Neo4j database offline. Ingesting in SQLite-only mode.")
        
    extractor = EntityExtractor()
    
    file_pattern = os.path.join(directory_path, "trajectories_*.json")
    files = glob.glob(file_pattern)
    
    print(f"Found {len(files)} trajectory files for ingestion.")
    for idx, filepath in enumerate(files):
        filename = os.path.basename(filepath)
        instance_id = filename.replace("trajectories_", "").replace(".json", "")
        
        try:
            ingest_swe_trajectory(
                store=store,
                projection=projection,
                extractor=extractor,
                traj_path=filepath,
                run_id=instance_id,
                goal=f"Resolve bug in {instance_id}"
            )
            print(f"[{idx+1}/{len(files)}] Ingested {instance_id}")
        except Exception as e:
            print(f"Failed to ingest {instance_id}: {e}")
            
    if projection:
        projection.close()
    store.close()

if __name__ == "__main__":
    # Run bulk ingestion on a local directory
    bulk_ingest("./downloaded_trajectories")
```

---

## 4. Research Evaluation Metrics

To publish a paper on **Semantic Agent Graph**, we evaluate the memory and execution performance against three core metrics:

### Metric 1: Recall Accuracy (Semantic Match Rate)
This measures how often the agent query tool returns a successful historical trajectory when encountering an error:

$$\text{Recall Accuracy} = \frac{\text{Successful Trajectories Retrieved}}{\text{Total Error Encounters}} \times 100$$

*   *Methodology:* Run 100 SWE-bench task instances. Record each time the agent encounters a compiler or test traceback. Check if the Cypher query matches similar canonical error/file nodes from previous runs in Neo4j.

### Metric 2: Execution Cost Reduction (Token Overhead)
Compare the size of the context window when using **SAG memory (Path Graph)** versus loading the full chat history or vector-based text RAG:
*   *SAG Path Graph Memory:* Exposes only the raw nodes and relationships of the relevant steps (highly structured, minimal tokens).
*   *Vector RAG:* Injects multiple full-text files or logs into the context (noisy, high token count).
*   *Evaluation:* Plot **Recall Tokens** vs. **Task Success Rate** to show the Pareto efficiency of SAG.

### Metric 3: Replay Caching Latency (RTT)
Measure the time taken to replay/fork a run using the prompt-hashing Cache Contract vs. executing live LLM API calls.
*   *Evaluation:* Compare:
    *   **Live RTT:** Cumulative time spent waiting for Gemini API responses.
    *   **Replay RTT:** Time spent fetching matches from SQLite cache.
    *   *Result:* Our benchmarks show that Replay Mode drops execution time from **~1.03s** to **~1.2ms** (an **860x speedup**), effectively reducing token RTT to zero.

---

## 5. Visualizing the Memory Graph in Neo4j

Once trajectories are bulk-ingested, launch the **Neo4j Browser** (`http://localhost:7474`) and execute these queries to visualize the episodic-semantic layers:

### View the Chronological Run Trajectory
To visualize the event sequence and caused-by lineages of a specific run:
```cypher
MATCH (r:Run {run_id: "swe_run_demo_1"})-[:CONTAINS]->(e:Event)
OPTIONAL MATCH (e)-[c:CAUSED_BY]->(cause:Event)
RETURN r, e, c, cause
```

### View Bloomed Semantic Connections
To see how different runs are semantically linked through global nodes (such as the canonical `Postgres` system or `TimeoutError` exceptions):
```cypher
MATCH (ent:Entity)
MATCH (e:Event)-[p:PROCESSED|MUTATED]->(ent)
MATCH (r:Run)-[:CONTAINS]->(e)
RETURN r, e, p, ent
```

### Trace Successful Pathways for Similar Errors
This Cypher query traverses from a target error to retrieve events from other successful runs that processed it:
```cypher
MATCH (target:Entity {name: "TimeoutError"})
MATCH (e:Event)-[:PROCESSED]->(target)
MATCH (r:Run)-[:CONTAINS]->(e)
MATCH (r)-[:CONTAINS]->(success:Event {type: "run.completed"})
MATCH path = (before:Event)-[:NEXT*..2]->(e)-[:NEXT*..2]->(after:Event)
RETURN path
```
