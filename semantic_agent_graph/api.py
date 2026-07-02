from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import json
import logging
import queue
import asyncio
from typing import Optional, Dict, Any, List

from semantic_agent_graph.store import SQLiteEventStore
from semantic_agent_graph.projection import Neo4jProjection
from semantic_agent_graph.models import Run, Event

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sag_api")

DB_PATH = os.environ.get("DB_PATH", "semantic_agent_graph.db")
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "password"))

class APILifecycle:
    def __init__(self):
        self.store = None
        self.projection = None

    def start(self):
        logger.info(f"Opening SQLite database at {DB_PATH}")
        self.store = SQLiteEventStore(DB_PATH)
        
        logger.info(f"Connecting to Neo4j at {NEO4J_URI}")
        try:
            self.projection = Neo4jProjection(uri=NEO4J_URI, auth=NEO4J_AUTH)
            # Ping database
            with self.projection.driver.session() as session:
                session.run("RETURN 1")
            logger.info("Connected to Neo4j successfully.")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            self.projection = None

    def stop(self):
        if self.store:
            logger.info("Closing SQLite connection.")
            self.store.close()
        if self.projection:
            logger.info("Closing Neo4j connection.")
            self.projection.close()

lifecycle = APILifecycle()

# Thread-safe queue for buffering SQLite events and runs
broadcast_queue = queue.Queue()

def on_event_appended(event: Event):
    broadcast_queue.put({"type": "event", "data": event})

def on_run_created(run: Run):
    broadcast_queue.put({"type": "run", "data": run})

@asynccontextmanager
async def lifespan(app: FastAPI):
    lifecycle.start()
    
    # Register SQLite event and run listener callbacks
    if lifecycle.store:
        lifecycle.store.register_listener(on_event_appended)
        lifecycle.store.register_run_listener(on_run_created)
        logger.info("Registered SQLite event and run listener callbacks.")

    # Start background task
    broadcast_task = asyncio.create_task(broadcast_worker())
    
    yield
    
    broadcast_task.cancel()
    try:
        await broadcast_task
    except asyncio.CancelledError:
        pass
    lifecycle.stop()

app = FastAPI(
    title="sAG Graph Dashboard API",
    description="Backend API serving SQLite episodic runs and Neo4j semantic projections for sAG.",
    version="1.0.0",
    lifespan=lifespan
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

async def broadcast_worker():
    """
    Background worker that polls the thread-safe queue,
    batches events/runs, and broadcasts them to all connected clients.
    """
    while True:
        try:
            items = []
            while not broadcast_queue.empty():
                try:
                    items.append(broadcast_queue.get_nowait())
                except queue.Empty:
                    break
            
            if items and manager.active_connections:
                events_to_send = []
                runs_to_send = []
                for item in items:
                    data = item["data"]
                    if item["type"] == "event":
                        ev_dict = data.model_dump() if hasattr(data, "model_dump") else data.dict()
                        events_to_send.append(ev_dict)
                    elif item["type"] == "run":
                        run_dict = data.model_dump() if hasattr(data, "model_dump") else data.dict()
                        run_dict["is_success"] = 0
                        runs_to_send.append(run_dict)
                
                if runs_to_send:
                    await manager.broadcast({
                        "type": "runs.created",
                        "runs": runs_to_send
                    })
                if events_to_send:
                    await manager.broadcast({
                        "type": "events.appended",
                        "events": events_to_send
                    })
            
            # Buffering delay of 1.0 second to satisfy low priority live watching request
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"WebSocket broadcast worker error: {e}")
            await asyncio.sleep(1.0)

@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    logger.info("New visualizer client connected via WebSocket.")
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Visualizer client disconnected from WebSocket.")
    except Exception as e:
        manager.disconnect(websocket)
        logger.error(f"WebSocket connection error: {e}")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow Vite dev client access
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

@app.get("/api/stats")
async def get_stats():
    """
    Returns database count metrics from both SQLite and Neo4j.
    """
    stats = {
        "sqlite": {"runs": 0, "events": 0},
        "neo4j": {"runs": 0, "events": 0, "entities": 0, "relationships": 0},
        "neo4j_available": lifecycle.projection is not None
    }
    
    # 1. SQLite Stats
    if lifecycle.store:
        try:
            with lifecycle.store._lock:
                cursor = lifecycle.store._conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM runs")
                stats["sqlite"]["runs"] = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM events")
                stats["sqlite"]["events"] = cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"SQLite stats error: {e}")

    # 2. Neo4j Stats
    if lifecycle.projection:
        try:
            with lifecycle.projection.driver.session() as session:
                # Count nodes by labels
                res = session.run("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS cnt")
                for r in res:
                    label = r["label"]
                    if label == "Run":
                        stats["neo4j"]["runs"] = r["cnt"]
                    elif label == "Event":
                        stats["neo4j"]["events"] = r["cnt"]
                    elif label == "Entity":
                        stats["neo4j"]["entities"] = r["cnt"]
                
                # Count relationships
                res_rels = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt")
                row = res_rels.single()
                if row:
                    stats["neo4j"]["relationships"] = row["cnt"]
        except Exception as e:
            logger.error(f"Neo4j stats error: {e}")

    return stats

@app.get("/api/runs")
async def get_runs():
    """
    Returns a list of all runs stored in SQLite, enriched with their success status.
    """
    if not lifecycle.store:
        raise HTTPException(status_code=500, detail="SQLite store not connected")
    try:
        runs = lifecycle.store.list_runs()
        # Query successful runs in a single query to ensure high performance
        with lifecycle.store._lock:
            cursor = lifecycle.store._conn.cursor()
            cursor.execute("SELECT DISTINCT run_id FROM events WHERE type IN ('run.completed', 'task.success')")
            successful_run_ids = {row[0] for row in cursor.fetchall()}
            
        results = []
        for r in runs:
            r_dict = r.model_dump() if hasattr(r, "model_dump") else r.dict()
            r_dict["is_success"] = 1 if r.run_id in successful_run_ids else 0
            results.append(r_dict)
        return results
    except Exception as e:
        logger.error(f"Failed to list runs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/runs/{run_id}/graph")
async def get_run_graph(run_id: str):
    """
    Queries Neo4j for the run's complete event trajectory and bridged entity nodes.
    """
    if not lifecycle.projection:
        raise HTTPException(status_code=500, detail="Neo4j projection not connected")

    nodes = []
    links = []
    seen_nodes = set()

    try:
        with lifecycle.projection.driver.session() as session:
            # 1. Fetch the Run node itself
            res_run = session.run("MATCH (r:Run {run_id: $run_id}) RETURN r", run_id=run_id)
            run_row = res_run.single()
            if run_row:
                run_props = dict(run_row["r"])
                run_node = {
                    "id": run_id,
                    "label": f"Run: {run_id}",
                    "group": "Run",
                    "type": "Run",
                    "properties": run_props
                }
                nodes.append(run_node)
                seen_nodes.add(run_id)

            # 2. Fetch all events in this run
            res_events = session.run(
                "MATCH (r:Run {run_id: $run_id})-[:CONTAINS]->(e:Event) RETURN e ORDER BY e.seq ASC",
                run_id=run_id
            )
            for r in res_events:
                ev_props = dict(r["e"])
                ev_id = ev_props.get("id")
                # Parse payload string if possible
                payload = {}
                if ev_props.get("payload"):
                    try:
                        payload = json.loads(ev_props["payload"])
                    except Exception:
                        pass
                
                label = ev_props.get("type", "Event")
                if label == "agent.step" and payload.get("action"):
                    label = f"Step: {payload['action'][:35]}..."
                elif label == "object.created" and payload.get("name"):
                    label = f"Processed: {payload['name']}"
                elif label == "relation.created" and payload.get("type"):
                    label = f"Relation: {payload['type']}"
                elif label == "run.backtracked" and payload.get("message"):
                    label = "BACKTRACKED"

                node = {
                    "id": ev_id,
                    "label": label,
                    "group": "Event",
                    "type": ev_props.get("type"),
                    "seq": ev_props.get("seq"),
                    "timestamp": ev_props.get("timestamp"),
                    "payload": payload,
                    "actor": ev_props.get("actor")
                }
                nodes.append(node)
                seen_nodes.add(ev_id)

                # Link from Run to Event (CONTAINS)
                links.append({
                    "source": run_id,
                    "target": ev_id,
                    "type": "CONTAINS"
                })

            # 3. Fetch NEXT sequence and CAUSED_BY relationships within the run
            res_rels = session.run(
                """
                MATCH (prev:Event {run_id: $run_id})-[rel:NEXT]->(curr:Event {run_id: $run_id})
                RETURN prev.id AS source, curr.id AS target, type(rel) AS type
                """,
                run_id=run_id
            )
            for r in res_rels:
                links.append({
                    "source": r["source"],
                    "target": r["target"],
                    "type": r["type"]
                })

            res_causes = session.run(
                """
                MATCH (curr:Event {run_id: $run_id})-[rel:CAUSED_BY]->(prev:Event {run_id: $run_id})
                RETURN curr.id AS source, prev.id AS target, type(rel) AS type
                """,
                run_id=run_id
            )
            for r in res_causes:
                links.append({
                    "source": r["source"],
                    "target": r["target"],
                    "type": r["type"]
                })

            # 4. Fetch the run's linked fork relationships if they exist
            res_forks = session.run(
                """
                MATCH (r:Run {run_id: $run_id})-[rel:FORKED_FROM]->(parent_ev:Event)
                RETURN r.run_id AS source, parent_ev.id AS target, type(rel) AS type
                """,
                run_id=run_id
            )
            for r in res_forks:
                links.append({
                    "source": r["source"],
                    "target": r["target"],
                    "type": r["type"]
                })

            # 5. Fetch all Entity nodes bridged to these events (PROCESSED)
            res_entities = session.run(
                """
                MATCH (e:Event {run_id: $run_id})-[rel:PROCESSED]->(ent:Entity)
                RETURN e.id AS source, ent.name AS target, type(rel) AS type, ent
                """,
                run_id=run_id
            )
            for r in res_entities:
                ent_props = dict(r["ent"])
                ent_name = ent_props.get("name")
                
                # Add entity node if not seen yet
                if ent_name not in seen_nodes:
                    node = {
                        "id": ent_name,
                        "label": ent_name,
                        "group": "Entity",
                        "type": ent_props.get("type", "Entity"),
                        "data": ent_props.get("data", {})
                    }
                    nodes.append(node)
                    seen_nodes.add(ent_name)

                # Link from Event to Entity (PROCESSED)
                links.append({
                    "source": r["source"],
                    "target": r["target"],
                    "type": r["type"]
                })

    except Exception as e:
        logger.error(f"Failed to query run graph for {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"nodes": nodes, "links": links}

@app.get("/api/semantic/graph")
async def get_semantic_graph():
    """
    Queries Neo4j for the global semantic network (Entities and their relationships).
    """
    if not lifecycle.projection:
        raise HTTPException(status_code=500, detail="Neo4j projection not connected")

    nodes = []
    links = []
    seen_nodes = set()

    try:
        with lifecycle.projection.driver.session() as session:
            # 1. Fetch all Entity nodes
            res_nodes = session.run("MATCH (ent:Entity) RETURN ent")
            for r in res_nodes:
                props = dict(r["ent"])
                name = props.get("name")
                
                data = {}
                if props.get("data"):
                    try:
                        data = json.loads(props["data"]) if isinstance(props["data"], str) else props["data"]
                    except Exception:
                        pass

                node = {
                    "id": name,
                    "label": name,
                    "group": "Entity",
                    "type": props.get("type", "Entity"),
                    "data": data
                }
                nodes.append(node)
                seen_nodes.add(name)

            # 2. Fetch relationships between entities
            res_rels = session.run(
                """
                MATCH (s:Entity)-[r]->(t:Entity)
                RETURN s.name AS source, t.name AS target, type(r) AS type, r.data AS data
                """
            )
            for r in res_rels:
                data = {}
                if r["data"]:
                    try:
                        data = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                    except Exception:
                        pass

                links.append({
                    "source": r["source"],
                    "target": r["target"],
                    "type": r["type"],
                    "data": data
                })

    except Exception as e:
        logger.error(f"Failed to query global semantic graph: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"nodes": nodes, "links": links}

# Mount frontend static build directory if it exists
static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "dist")
if os.path.exists(static_dir):
    from fastapi.responses import FileResponse
    logger.info(f"Mounting frontend assets from {os.path.join(static_dir, 'assets')}")
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

    # Serve index.html for all other non-API routes to support client-side routing
    @app.get("/{catchall:path}")
    async def serve_frontend(catchall: str):
        if catchall.startswith("api"):
            raise HTTPException(status_code=404, detail="API route not found")
        
        index_file = os.path.join(static_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="index.html not found")
else:
    logger.warning(f"Frontend static directory not found at {static_dir}. API will run in standalone mode.")
