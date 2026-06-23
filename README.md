# Semantic-Agent-Graph

An event-sourced episodic-semantic memory framework for branchable agent memory using SQLite and Neo4j.

## Architecture

- **Episodic Trajectory Layer**: Captures step-by-step logs, tool executions, and forks in an append-only event store (SQLite).
- **Semantic Layer**: Projects events into a Neo4j graph and bridges them to canonical entity nodes (such as systems, variables, and error codes).
- **Branchable Agent Loop**: Replays trajectories deterministically from cached LLM/tool event logs and forks runs.
- **Graph Memory Retrieval**: Queries paths and relationships to return raw Neo4j path graphs to the agent.
