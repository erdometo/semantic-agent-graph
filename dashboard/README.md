# Semantic Agent Graph (sAG) Dashboard

This folder contains the interactive **WebGL Force-Directed Graph Visualizer & Inspector** frontend for the **Semantic Agent Graph (sAG)** framework. It is built as a single-page application (SPA) using React, TypeScript, and Vite.

---

## Core Technologies
*   **React 18 & TypeScript**: Component-driven UI and strict type safety.
*   **Vite**: Lightning-fast dev builds and optimized asset compiling.
*   **react-force-graph (2D & 3D)**: High-performance canvas-based and WebGL-based (Three.js) graph physics visualizer.
*   **Lucide React**: Clean, modern iconography system.
*   **Vanilla CSS**: Premium dark-mode glassmorphism design system.

---

## Key Features

1.  **Dual Layout Engine**:
    *   **2D Force-Directed Layout**: Draws nodes as high-speed canvas vectors with custom styling, edge types, particle paths, and text labels.
    *   **3D WebGL Layout**: Renders nodes as 3D meshes (Spheres for events/runs, Cubes for entities) inside a Three.js light-lit space with camera orbit/zoom controls.
2.  **Chronological Timeline Layout Mode**:
    *   Positions events linearly from left to right along the X-axis based on their sequence index.
    *   Separates distinct execution runs (e.g. parent/child forks) into clean parallel horizontal lanes along the Y-axis.
    *   Aligns Entity nodes horizontally above the timeline exactly at the average step index they were referenced.
3.  **Real-Time WebSocket Sync**:
    *   Establishes a persistent `/api/ws` subscription to the FastAPI backend with automatic reconnection.
    *   Dynamically updates the sidebar runs list when new runs are created.
    *   Refreshes the active graph layout when new episodic events are streamed from the server.
4.  **Tabbed Inspector Sidepanel**:
    *   **Overview & Details Tab**: Displays context cards containing goals, timestamps, reflection logs, tool executions, errors, and clickable lineage fork jumps.
    *   **Raw JSON Payload Tab**: Renders the complete database node schema object in a styled code block.

---

## Setup & Local Development

### 1. Installation
Install Node.js dependencies:
```bash
npm install
```

### 2. Run Development Server
Start the local Vite server:
```bash
npm run dev
```
By default, this launches at `http://localhost:5173/`. 
*Note: Vite config is pre-configured with a reverse proxy mapping `/api/*` to `http://localhost:8000/api/*` so that API calls resolve correctly to your local FastAPI backend.*

### 3. Production Build
Compile and bundle the production assets into `dist/`:
```bash
npm run build
```
These compiled assets are automatically served by the FastAPI uvicorn daemon under the root route `/`.
