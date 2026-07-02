import logging
from typing import Optional, Dict, Any, List

from semantic_agent_graph.models import Run, Event
from semantic_agent_graph.store import SQLiteEventStore
from semantic_agent_graph.projection import Neo4jProjection
from semantic_agent_graph.runtime import ReactiveRuntime

logger = logging.getLogger(__name__)


class PredictiveBacktrackingEngine:
    """
    Predictive dead-end detection and backtracking engine for sAG.
    Analyzes historical trajectories in Neo4j to predict failure paths and backtrack.
    """

    def __init__(self, runtime: ReactiveRuntime):
        self.runtime = runtime
        self.store = runtime.store
        self.projection = runtime.projection

    def check_dead_end(
        self,
        run_id: str,
        threshold_entities: int = 2,
        threshold_failure_rate: float = 0.75
    ) -> Optional[Dict[str, Any]]:
        """
        Queries Neo4j to check if the current run's prefix matches historical runs that failed.

        Returns:
            A dictionary containing dead-end details if a dead-end is predicted, otherwise None.
        """
        if not self.projection:
            logger.warning("Neo4j projection is not configured. Skipping dead-end check.")
            return None

        # Cypher query to:
        # 1. Collect all entities processed in the current run.
        # 2. Find other historical runs that processed the same entities.
        # 3. Check terminal events of those runs to determine success vs failure.
        query = """
        MATCH (r:Run {run_id: $run_id})-[:CONTAINS]->(e:Event)-[:PROCESSED]->(ent:Entity)
        WITH collect(DISTINCT ent.name) AS current_entities
        
        MATCH (h_run:Run)-[:CONTAINS]->(he:Event)-[:PROCESSED]->(shared:Entity)
        WHERE h_run.run_id <> $run_id AND shared.name IN current_entities
        
        WITH h_run, collect(DISTINCT shared.name) AS matched_names, count(DISTINCT shared.name) AS matched_count
        WHERE matched_count >= $threshold_entities
        
        MATCH (h_run)-[:CONTAINS]->(terminal:Event)
        WHERE terminal.type IN ["run.completed", "run.failed"]
        
        RETURN h_run.run_id AS hist_run_id, terminal.type AS outcome, matched_names, matched_count
        """

        try:
            with self.projection.driver.session() as session:
                res = session.run(query, run_id=run_id, threshold_entities=threshold_entities)
                records = list(res)

            if not records:
                return None

            # Aggregate outcomes globally for matching historical runs
            total_runs = len(records)
            failed_runs = [r["hist_run_id"] for r in records if r["outcome"] == "run.failed"]
            failed_count = len(failed_runs)

            failure_rate = failed_count / total_runs if total_runs > 0 else 0.0

            if failure_rate >= threshold_failure_rate:
                # We have a predicted dead end!
                # Find all matched entity names
                all_matched_entities = set()
                for r in records:
                    all_matched_entities.update(r["matched_names"])
                matched_entities_list = list(all_matched_entities)

                # Now find the first matched event in the current run's SQLite event stream.
                current_events = self.store.get_events(run_id)
                first_matched_event = None

                for ev in current_events:
                    # Look for object.created events mapping to our matched entities
                    if ev.type == "object.created" and ev.payload.get("name") in all_matched_entities:
                        first_matched_event = ev
                        break

                if first_matched_event:
                    return {
                        "predicted_dead_end": True,
                        "failure_rate": failure_rate,
                        "failed_runs": failed_runs,
                        "matched_entities": matched_entities_list,
                        "first_matched_event_id": first_matched_event.id,
                        "first_matched_seq": first_matched_event.seq
                    }
        except Exception as e:
            logger.error(f"Error checking dead end for run {run_id}: {e}", exc_info=True)

        return None

    def backtrack(self, run_id: str, new_run_id: str, dead_end_info: Dict[str, Any]) -> str:
        """
        Backtracks the run by forking it to the last stable state and injecting negative feedback.
        """
        first_matched_seq = dead_end_info["first_matched_seq"]
        current_events = self.store.get_events(run_id)

        # Find the event immediately preceding the first matched event
        stable_event = None
        for ev in current_events:
            if ev.seq is not None and ev.seq < first_matched_seq:
                if stable_event is None or ev.seq > stable_event.seq:
                    stable_event = ev

        if not stable_event:
            # If no preceding event exists, backtrack to the first event
            stable_event = current_events[0]

        logger.info(f"Backtracking run {run_id} to stable event {stable_event.id} (seq {stable_event.seq})")

        # Perform fork in SQLite and Neo4j
        self.runtime.fork_run(
            parent_run_id=run_id,
            new_run_id=new_run_id,
            forked_at_event_id=stable_event.id
        )

        # Emit negative feedback run.backtracked event in the new run
        feedback_message = (
            f"Backtracked to escape a predicted dead end. "
            f"Do not repeat actions that touch: {dead_end_info['matched_entities']}."
        )

        with self.runtime.active_run(new_run_id):
            self.runtime.emit("run.backtracked", {
                "message": feedback_message,
                "failed_historical_runs": dead_end_info["failed_runs"],
                "matched_entities": dead_end_info["matched_entities"]
            })
            self.runtime.dispatch_loop()

        return new_run_id
