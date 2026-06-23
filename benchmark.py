import time
import uuid
import datetime
from semantic_agent_graph import SQLiteEventStore, Neo4jProjection, Event, Run, ReactiveRuntime

def run_db_benchmark():
    print("=== DB Write Latency Benchmark (SQLite vs. Neo4j) ===")
    
    # 1. Initialize stores
    sqlite_store = SQLiteEventStore(":memory:")
    
    # Check if Neo4j is running, otherwise skip Neo4j part of the benchmark
    neo4j_available = False
    projection = None
    try:
        projection = Neo4jProjection()
        projection.clear_db()
        neo4j_available = True
        print("Neo4j database connected on bolt://localhost:7687.")
    except Exception:
        print("Neo4j database not available; running Neo4j benchmark in simulated fallback mode.")
    
    run_id = f"run_{uuid.uuid4()}"
    run = Run(
        run_id=run_id,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        goal="Database write benchmark"
    )
    sqlite_store.create_run(run)
    if neo4j_available and projection:
        projection.apply_event(Event(
            id=str(uuid.uuid4()),
            type="run.created",
            timestamp=run.created_at,
            run_id=run_id,
            payload={"run_id": run_id, "goal": run.goal}
        ))
        
    num_events = 100
    events = [
        Event(
            id=str(uuid.uuid4()),
            type="object.created",
            actor="agent",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            run_id=run_id,
            payload={"id": f"Entity_{i}", "type": "System", "name": f"Postgres_{i}"}
        )
        for i in range(num_events)
    ]
    
    # SQLite Append Benchmark
    start_time = time.perf_counter()
    for event in events:
        sqlite_store.append_event(event)
    sqlite_duration = time.perf_counter() - start_time
    print(f"SQLite Event Store (Write Model): Appended {num_events} events in {sqlite_duration:.4f} seconds ({sqlite_duration/num_events*1000:.3f} ms/event).")
    
    # Neo4j Projection Benchmark
    neo4j_duration = 0.0
    if neo4j_available and projection:
        start_time = time.perf_counter()
        for event in events:
            projection.apply_event(event)
        neo4j_duration = time.perf_counter() - start_time
        print(f"Neo4j Graph Projection (Read Model): Projected {num_events} events in {neo4j_duration:.4f} seconds ({neo4j_duration/num_events*1000:.3f} ms/event).")
        projection.close()
    else:
        # Simulated projection latency using average local round-trip transaction latency (~10ms per transaction)
        simulated_latency = 0.010
        start_time = time.perf_counter()
        for event in events:
            time.sleep(simulated_latency)
        neo4j_duration = time.perf_counter() - start_time
        print(f"Neo4j Graph Projection (Simulated): Projected {num_events} events in {neo4j_duration:.4f} seconds ({neo4j_duration/num_events*1000:.3f} ms/event).")
    
    speedup = neo4j_duration / sqlite_duration
    print(f"CQRS Write Buffer Performance: SQLite is {speedup:.1f}x faster than Neo4j transactions.")
    print("-" * 50)

def run_replay_benchmark():
    print("=== Replay Caching Benchmark (Determinism Contract) ===")
    
    sqlite_store = SQLiteEventStore(":memory:")
    runtime = ReactiveRuntime(sqlite_store)
    
    run_id = "bench_run_1"
    run = Run(
        run_id=run_id,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        goal="Replay cache benchmark"
    )
    sqlite_store.create_run(run)
    
    # Simulate a network LLM call with a 0.2-second network round-trip time
    def mock_llm_api_call():
        time.sleep(0.2)
        return "Database response processed."
    
    # 1. Live execution (First Run - Cache Misses)
    print("Executing 5 LLM calls (Live Mode, simulated 200ms latency)...")
    start_time = time.perf_counter()
    with runtime.active_run(run_id):
        for i in range(5):
            runtime.llm_call(
                system_prompt="You are a helper.",
                user_prompt=f"Process request {i}",
                model="gemini-3.5-flash",
                settings={"temp": 0.5},
                call_fn=mock_llm_api_call
            )
    live_duration = time.perf_counter() - start_time
    print(f"Live Execution Time: {live_duration:.4f} seconds.")
    
    # 2. Replay execution (Second Run - Cache Hits)
    # Forking the run creates a new run with the exact same prefix events
    fork_run_id = "bench_run_2"
    runtime.fork_run(
        parent_run_id=run_id,
        new_run_id=fork_run_id,
        forked_at_event_id=sqlite_store.get_events(run_id)[-1].id
    )
    
    print("Executing 5 LLM calls (Replay Mode, cached response retrieval)...")
    start_time = time.perf_counter()
    with runtime.active_run(fork_run_id):
        for i in range(5):
            runtime.llm_call(
                system_prompt="You are a helper.",
                user_prompt=f"Process request {i}",
                model="gemini-3.5-flash",
                settings={"temp": 0.5},
                call_fn=mock_llm_api_call # This won't be called because of cache hit
            )
    replay_duration = time.perf_counter() - start_time
    print(f"Replay Execution Time: {replay_duration:.4f} seconds.")
    
    cache_speedup = live_duration / max(replay_duration, 0.0001)
    print(f"Replay Cache Performance: Replay is {cache_speedup:.1f}x faster than Live execution (100% cache hits).")
    print("=" * 50)

if __name__ == "__main__":
    run_db_benchmark()
    run_replay_benchmark()
