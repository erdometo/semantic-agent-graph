import os
import re
import json
import uuid
import datetime
import pandas as pd
from semantic_agent_graph import (
    SQLiteEventStore,
    Neo4jProjection,
    EntityExtractor,
    Event,
    Run,
    Entity,
    Relation,
    ingest_swe_trajectory,
)

# Configure paths and connection settings
DB_PATH = "semantic_agent_graph.db"
NEO4J_URI = "bolt://localhost:7687"
NEO4J_AUTH = ("neo4j", "password")
# Dynamic URL generation will be used in main()

class BulkEntityExtractor(EntityExtractor):
    """
    Subclass of EntityExtractor optimized for bulk ingestion of SWE-agent logs.
    Performs fast, rule-based extraction of developer concepts (files, tools, exceptions)
    without making external LLM calls, ensuring 1000x higher throughput and 0% rate limit errors.
    """
    def __init__(self):
        super().__init__(api_key="")  # Pass empty key to disable OpenRouter calls

    def extract(self, text: str) -> tuple[list[Entity], list[Relation]]:
        # 1. Use the base class regex parser for postgres/timeout logs
        base_entities, base_relations = self._extract_via_regex(text)
        
        # 2. Extract files: patterns matching standard paths ending in extensions (py, json, txt, sh, etc.)
        file_pattern = re.compile(
            r"\b([a-zA-Z0-9_\-\/]+\.(?:py|json|txt|md|html|yml|yaml|sh|js|css|cfg|toml|ini))\b"
        )
        files = file_pattern.findall(text)
        
        # 3. Extract tools/commands: developer actions
        tool_pattern = re.compile(
            r"\b(pytest|git|grep|find|pip|python|poetry|lexicon|autopep8|black|flake8|find_files|view_file|edit|submit)\b",
            re.IGNORECASE
        )
        tools = tool_pattern.findall(text)
        
        # 4. Extract errors: typical Python exceptions and standard failures
        error_pattern = re.compile(
            r"\b([a-zA-Z0-9_]*(?:Error|Exception|Fail|Failure|Traceback|SyntaxError|TypeError|AttributeError|ValueError|KeyError))\b"
        )
        errors = error_pattern.findall(text)
        
        extracted_entities = list(base_entities)
        extracted_relations = list(base_relations)
        added_ids = {ent.id for ent in extracted_entities}
        
        # Add File entities
        file_nodes = []
        for f in set(files):
            # Exclude short fragments or things that are not files
            if "/" not in f and "." not in f:
                continue
            f_id = f.replace("/", "_").replace(".", "_")
            if f_id not in added_ids:
                # Limit name length for cleaner visualization
                name = f if len(f) < 80 else f[-80:]
                ent = Entity(id=f_id, type="File", name=name, data={})
                extracted_entities.append(ent)
                added_ids.add(f_id)
                file_nodes.append(ent)
                
        # Add Tool entities
        tool_nodes = []
        for t in set(tools):
            t_name = t.capitalize()
            t_id = t_name
            if t_id not in added_ids:
                ent = Entity(id=t_id, type="Tool", name=t_name, data={})
                extracted_entities.append(ent)
                added_ids.add(t_id)
                tool_nodes.append(ent)
                
        # Add Error entities
        error_nodes = []
        for e in set(errors):
            # Ignore base "Error" or "Exception" word if caught alone
            if e in ["Error", "Exception", "Traceback"]:
                continue
            e_id = e
            if e_id not in added_ids:
                ent = Entity(id=e_id, type="Error", name=e, data={})
                extracted_entities.append(ent)
                added_ids.add(e_id)
                error_nodes.append(ent)
                
        # Connect Tool to File (TOUCHED)
        for t_node in tool_nodes:
            for f_node in file_nodes:
                rel_id = f"{t_node.id}-TOUCHED-{f_node.id}"
                extracted_relations.append(Relation(
                    id=rel_id,
                    type="TOUCHED",
                    source=t_node.id,
                    target=f_node.id,
                    data={}
                ))
                
        # Connect Tool to Error (ENCOUNTERED)
        for t_node in tool_nodes:
            for e_node in error_nodes:
                rel_id = f"{t_node.id}-ENCOUNTERED-{e_node.id}"
                extracted_relations.append(Relation(
                    id=rel_id,
                    type="ENCOUNTERED",
                    source=t_node.id,
                    target=e_node.id,
                    data={}
                ))
                
        return extracted_entities, extracted_relations

def parse_ai_text(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    
    # Catch first markdown code block
    match = re.search(r"```[a-zA-Z0-9_\-]*\n(.*?)\n```", text, re.DOTALL)
    if match:
        action = match.group(1).strip()
        thought = text[:match.start()].strip()
        thought = re.sub(r"^DISCUSSION\s*", "", thought, flags=re.IGNORECASE).strip()
        return thought, action
    else:
        thought = re.sub(r"^DISCUSSION\s*", "", text, flags=re.IGNORECASE).strip()
        return thought, ""

def parse_trajectory_turns(trajectory) -> list[dict]:
    steps = []
    i = 2  # Skip system instructions and issue definition
    n = len(trajectory)
    
    while i < n:
        turn = trajectory[i]
        role = turn.get("role")
        
        if role == "ai" or role == "assistant":
            thought, action = parse_ai_text(turn.get("text", ""))
            observation = ""
            if i + 1 < n:
                next_turn = trajectory[i + 1]
                if next_turn.get("role") == "user":
                    observation = next_turn.get("text", "")
                    observation = re.sub(
                        r"^\(Open file:.*?\)\s*\(Current directory:.*?\)\s*bash-\$\s*", 
                        "", 
                        observation, 
                        flags=re.DOTALL
                    ).strip()
            
            steps.append({
                "thought": thought,
                "action": action,
                "observation": observation
            })
            i += 2
        else:
            i += 1
            
    return steps

def main():
    print("=== Blooming-ActiveGraph Bulk Dataset Ingestor (Regex-Rich) ===")
    
    # 1. Initialize databases
    print(f"Connecting to SQLite: {DB_PATH}")
    # Remove existing local SQLite database to start fresh
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            print("Cleared existing SQLite database file.")
        except Exception as e:
            print(f"Warning: Could not remove {DB_PATH}: {e}")
            
    store = SQLiteEventStore(DB_PATH)
    
    print(f"Connecting to Neo4j: {NEO4J_URI}")
    projection = None
    try:
        projection = Neo4jProjection(uri=NEO4J_URI, auth=NEO4J_AUTH)
        print("Connected to Neo4j database successfully.")
        print("Clearing past Neo4j graph nodes and relationships...")
        projection.clear_db()
    except Exception as e:
        print(f"ERROR: Cannot connect to Neo4j: {e}")
        return

    # Use the optimized BulkEntityExtractor
    extractor = BulkEntityExtractor()
    print("Initialized BulkEntityExtractor (rule-based offline parser).")

    # 2. Load dataset dynamically
    dfs = []
    chunk_idx = 0
    while True:
        url = f"https://huggingface.co/api/datasets/nebius/SWE-agent-trajectories/parquet/default/train/{chunk_idx}.parquet"
        print(f"Downloading parquet chunk {chunk_idx} from Hugging Face: {url}")
        try:
            df_part = pd.read_parquet(url)
            print(f"Loaded parquet table {chunk_idx}. Total instances: {len(df_part)}")
            dfs.append(df_part)
            chunk_idx += 1
        except Exception as e:
            print(f"Finished scanning dataset chunks. (Status at chunk {chunk_idx}: {e})")
            break

    if not dfs:
        print("ERROR: No dataset chunks could be loaded.")
        return

    df = pd.concat(dfs, ignore_index=True)
    print(f"Merged parquet tables. Total combined instances: {len(df)}")

    # Filter for successful trajectories and drop duplicate instances
    success_df = df[df["target"] == True].drop_duplicates(subset=["instance_id"])
    print(f"Filtered for successful agent runs (target=True) and dropped duplicates. Count: {len(success_df)}")

    # Filter for failed trajectories and drop duplicate instances
    failure_df = df[df["target"] == False].drop_duplicates(subset=["instance_id"])
    print(f"Filtered for failed agent runs (target=False) and dropped duplicates. Count: {len(failure_df)}")

    # Select all successful runs and a representative subset of failed runs (e.g., 200 failed runs)
    failed_subset = failure_df.head(200)
    print(f"Selected 200 failed runs for ingestion to represent real failure trajectories.")

    # Combine both datasets
    combined_df = pd.concat([success_df, failed_subset], ignore_index=True)
    max_runs_to_ingest = len(combined_df)
    runs_subset = combined_df
    
    print(f"Ingesting all {max_runs_to_ingest} trajectories (successes and failures)...")
    
    temp_json_path = "temp_trajectory.json"
    ingested_count = 0
    
    for idx, row in runs_subset.iterrows():
        instance_id = row["instance_id"]
        trajectory = row["trajectory"]
        is_success = bool(row["target"])
        
        # Extract goal
        goal = "Resolve GitHub issue."
        if len(trajectory) > 1:
            first_user_turn = trajectory[1]
            if first_user_turn.get("role") == "user":
                goal_text = first_user_turn.get("text", "")
                goal_text = goal_text.replace(
                    "We're currently solving the following issue within our repository. Here's the issue text:\nISSUE:\n", 
                    ""
                )
                lines = goal_text.strip().split("\n")
                goal = lines[0][:250] if lines else goal_text[:250]
                
        # Parse turns into steps
        steps = parse_trajectory_turns(trajectory)
        if not steps:
            continue
            
        with open(temp_json_path, "w", encoding="utf-8") as f:
            json.dump({"trajectory": steps}, f, indent=2)
            
        try:
            run_id = f"swe_{instance_id.replace('-', '_').replace('.', '_')}"
            ingest_swe_trajectory(
                store=store,
                projection=projection,
                extractor=extractor,
                traj_path=temp_json_path,
                run_id=run_id,
                goal=goal,
                is_success=is_success
            )
            ingested_count += 1
            print(f"[{ingested_count}/{max_runs_to_ingest}] Ingested {run_id} ({len(steps)} steps) | Success={is_success}")
        except Exception as e:
            print(f"Failed to ingest {instance_id}: {e}")
            
    # Clean up temp file
    if os.path.exists(temp_json_path):
        os.remove(temp_json_path)
        
    print("\n==================================================")
    print(f"Ingestion completed successfully! Ingested {ingested_count} runs.")
    print("==================================================")
    
    # 3. Print database statistics
    if projection:
        try:
            with projection.driver.session() as session:
                res_nodes = session.run("MATCH (n) RETURN labels(n) AS labels, count(n) AS cnt")
                print("Neo4j Node Statistics:")
                for r in res_nodes:
                    print(f"  - Labels: {r['labels']} | Count: {r['cnt']}")
                    
                res_rels = session.run("MATCH ()-[r]->() RETURN type(r) AS rel_type, count(r) AS cnt")
                print("Neo4j Relationship Statistics:")
                for r in res_rels:
                    print(f"  - Relation Type: {r['rel_type']} | Count: {r['cnt']}")
        except Exception as e:
            print(f"Failed to query Neo4j statistics: {e}")
            
    store.close()
    if projection:
        projection.close()

if __name__ == "__main__":
    main()
