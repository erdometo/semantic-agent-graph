import sqlite3
import json
import threading
import datetime
from typing import List, Optional
from semantic_agent_graph.models import Event, Run

class SQLiteEventStore:
    """
    SQLite-backed event store for ActiveGraph.
    Provides thread-safe access to append-only event streams and run metadata management.
    """
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    parent_run_id TEXT,
                    forked_at_event_id TEXT,
                    label TEXT,
                    created_at TEXT NOT NULL,
                    goal TEXT,
                    frame_id TEXT
                );
            """)
            cursor.execute("""
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
            """)
            self._conn.commit()

    def create_run(self, run: Run) -> None:
        """
        Creates a new run entry in the runs table.
        """
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO runs (run_id, parent_run_id, forked_at_event_id, label, created_at, goal, frame_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    run.run_id,
                    run.parent_run_id,
                    run.forked_at_event_id,
                    run.label,
                    run.created_at,
                    run.goal,
                    run.frame_id
                ))
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Run with ID '{run.run_id}' already exists.") from e

    def get_run(self, run_id: str) -> Optional[Run]:
        """
        Retrieves run metadata for the specified run_id.
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Run(
                run_id=row["run_id"],
                parent_run_id=row["parent_run_id"],
                forked_at_event_id=row["forked_at_event_id"],
                label=row["label"],
                created_at=row["created_at"],
                goal=row["goal"],
                frame_id=row["frame_id"]
            )

    def list_runs(self) -> List[Run]:
        """
        Lists all runs in the database, ordered by creation time descending.
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM runs ORDER BY created_at DESC")
            rows = cursor.fetchall()
            return [
                Run(
                    run_id=row["run_id"],
                    parent_run_id=row["parent_run_id"],
                    forked_at_event_id=row["forked_at_event_id"],
                    label=row["label"],
                    created_at=row["created_at"],
                    goal=row["goal"],
                    frame_id=row["frame_id"]
                )
                for row in rows
            ]

    def append_event(self, event: Event) -> None:
        """
        Appends a new event to the events stream. 
        Updates the Event's 'seq' attribute with the auto-incremented value.
        """
        with self._lock:
            cursor = self._conn.cursor()
            # First verify the run exists
            cursor.execute("SELECT 1 FROM runs WHERE run_id = ?", (event.run_id,))
            if not cursor.fetchone():
                raise ValueError(f"Cannot append event. Run with ID '{event.run_id}' does not exist.")
            
            try:
                cursor.execute("""
                    INSERT INTO events (id, type, actor, payload, frame_id, caused_by, timestamp, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event.id,
                    event.type,
                    event.actor,
                    json.dumps(event.payload),
                    event.frame_id,
                    event.caused_by,
                    event.timestamp,
                    event.run_id
                ))
                self._conn.commit()
                event.seq = cursor.lastrowid
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Event with ID '{event.id}' already exists in run '{event.run_id}'.") from e

    def get_events(self, run_id: str) -> List[Event]:
        """
        Retrieves all events for a given run_id ordered sequentially.
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM events WHERE run_id = ? ORDER BY seq ASC", (run_id,))
            rows = cursor.fetchall()
            events = []
            for row in rows:
                events.append(Event(
                    seq=row["seq"],
                    id=row["id"],
                    type=row["type"],
                    actor=row["actor"],
                    payload=json.loads(row["payload"]),
                    frame_id=row["frame_id"],
                    caused_by=row["caused_by"],
                    timestamp=row["timestamp"],
                    run_id=row["run_id"]
                ))
            return events

    def fork_run(
        self,
        parent_run_id: str,
        new_run_id: str,
        forked_at_event_id: str,
        label: Optional[str] = None,
        created_at: Optional[str] = None
    ) -> Run:
        """
        Forks a new run from an existing run at a specific event ID.
        Copies all events from the parent run up to and including the fork point event
        to the new run event log.
        """
        if not created_at:
            created_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        with self._lock:
            cursor = self._conn.cursor()
            
            # Verify the parent run exists
            cursor.execute("SELECT * FROM runs WHERE run_id = ?", (parent_run_id,))
            parent = cursor.fetchone()
            if not parent:
                raise ValueError(f"Parent run with ID '{parent_run_id}' not found.")
            
            # Verify the new run ID doesn't already exist
            cursor.execute("SELECT 1 FROM runs WHERE run_id = ?", (new_run_id,))
            if cursor.fetchone():
                raise ValueError(f"Run with ID '{new_run_id}' already exists.")

            # Retrieve all parent events sequentially
            cursor.execute("SELECT * FROM events WHERE run_id = ? ORDER BY seq ASC", (parent_run_id,))
            parent_events = cursor.fetchall()

            # Locate the fork point event
            fork_index = -1
            for idx, row in enumerate(parent_events):
                if row["id"] == forked_at_event_id:
                    fork_index = idx
                    break
            
            if fork_index == -1:
                raise ValueError(f"Fork point event '{forked_at_event_id}' not found in parent run '{parent_run_id}'.")

            # Extract events to copy
            events_to_copy = parent_events[:fork_index + 1]

            # Insert the new run metadata
            new_label = label or f"Fork of {parent_run_id} at {forked_at_event_id}"
            cursor.execute("""
                INSERT INTO runs (run_id, parent_run_id, forked_at_event_id, label, created_at, goal, frame_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                new_run_id,
                parent_run_id,
                forked_at_event_id,
                new_label,
                created_at,
                parent["goal"],
                parent["frame_id"]
            ))

            # Copy events sequentially to the new run
            for row in events_to_copy:
                cursor.execute("""
                    INSERT INTO events (id, type, actor, payload, frame_id, caused_by, timestamp, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row["id"],
                    row["type"],
                    row["actor"],
                    row["payload"], # Stored as JSON string
                    row["frame_id"],
                    row["caused_by"],
                    row["timestamp"],
                    new_run_id
                ))

            self._conn.commit()

            return Run(
                run_id=new_run_id,
                parent_run_id=parent_run_id,
                forked_at_event_id=forked_at_event_id,
                label=new_label,
                created_at=created_at,
                goal=parent["goal"],
                frame_id=parent["frame_id"]
            )

    def close(self):
        """
        Closes the database connection.
        """
        with self._lock:
            self._conn.close()
