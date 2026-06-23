# testing-strategy (TESTING.md)

This document details the testing architecture, environment setup, and test execution procedures for the **Semantic Agent Graph (SAG)** framework.

---

## 1. Testing Philosophy

The test suite is built to ensure correctness, isolation, and robustness under both connected and disconnected environments.

*   **Episodic Isolation:** We use SQLite's `:memory:` databases in tests to guarantee that each test case starts with a fresh, clean execution log and avoids polluting the filesystem.
*   **Decoupled Graph Projection:** The database projection engine is designed to handle unreachable or offline Neo4j databases gracefully. Tests fall back to simulating operations in SQLite-only mode if no Neo4j driver is active.
*   **Hermetic LLM Mocking:** We verify LLM behaviors (such as caching and structured output) using local programmatic mocks or deterministic functions. This guarantees that running the test suite does not require a live Gemini API key, preventing network latency and API charges during development.

---

## 2. Test Execution Commands

All tests are managed through **Poetry** and **pytest**. First, ensure dependencies are fully installed:
```bash
poetry install --no-root
```

### Run the Entire Test Suite
Executes all 14 unit tests across the repository:
```bash
poetry run pytest
```

### Run Tests with Console Output
Prints execution outputs, logging messages, and standard outputs (useful for verifying debug text):
```bash
poetry run pytest -s
```

### Run a Specific Test File
Run only runtime, extraction, or parser tests:
```bash
# Runtime events, cache contract, and reactive loop behaviors
poetry run pytest tests/test_runtime.py

# Regex patterns, LLM schema fallback, and normalization lookup logic
poetry run pytest tests/test_extraction.py

# Ingestion parser and turn verification
poetry run pytest tests/test_parser.py
```

### Run a Single Test Case
Run a target test case by its signature:
```bash
poetry run pytest tests/test_runtime.py::test_llm_caching
```

---

## 3. Integration Testing with Neo4j

While unit tests run isolated from external services, you can verify the end-to-end Cypher query projections and sub-graph memory retrieval using a local Neo4j instance.

### A. Spin up a Local Neo4j Container
Start an ephemeral Neo4j database using Docker:
```bash
docker run -d \
  --name neo4j-sag-test \
  -p 7474:7474 \
  -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:5.12.0
```

### B. Verify Integration in the Demo
With the docker container running, run the end-to-end simulation script:
```bash
poetry run python demo.py
```
This script will:
1.  Connect to `bolt://localhost:7687` with credentials `neo4j/password`.
2.  Clear the database of any old nodes.
3.  Simulate a database connection error, project its episodic-semantic layers, and resolve the error by flushing the DNS cache.
4.  Replay and query the memory graph in Neo4j to pull out the successful resolution pathway.
5.  Perform a run fork to verify branch copying.

---

## 4. Test Suite Map

*   **[test_runtime.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/tests/test_runtime.py):**
    -   `test_runtime_initialization`: Verifies correct registration of default attributes.
    -   `test_behavior_registration`: Validates manual and decorator-based behavior bindings.
    -   `test_global_behavior_decorator`: Checks registration of globally declared behaviors.
    -   `test_emit_and_context`: Asserts that context vars restrict emissions to active run scopes.
    -   `test_dispatch_loop`: Validates sequential dispatching and event chaining.
    -   `test_llm_caching`: Verifies that identical prompt payloads result in cache hits and prevent redundant calls.
    -   `test_fork_run_and_projection`: Validates event copying and Neo4j projection synchronization during branch forks.
*   **[test_extraction.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/tests/test_extraction.py):**
    -   `test_regex_extraction`: Verifies pattern matches for systems (pg, redis), configurations, and error tracebacks.
    -   `test_normalization_canonical`: Checks normalization lookup tables (e.g. mapping `pg` to `Postgres` and `mysql` to `MySQL`).
    -   `test_llm_extraction_fallback`: Asserts that when the client is uninitialized, the extractor falls back to returning empty schemas.
*   **[test_parser.py](file:///c:/Users/ASUS/Desktop/projects/Agent-Log-Graph/tests/test_parser.py):**
    -   `test_parser_swe_ingest`: Validates that standard SWE-agent trajectory schemas parse successfully and insert correctly into the SQLite event log database.
