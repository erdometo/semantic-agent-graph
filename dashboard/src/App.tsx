import { useState, useEffect, useMemo, useRef } from 'react';
import {
  Undo2,
  GitFork,
  Database,
  Network,
  Activity,
  Search,
  RefreshCw,
  FileText,
  Terminal,
  Sliders,
  Info,
  Globe,
  Cpu,
  Maximize2
} from 'lucide-react';
import './App.css';
import { GraphCanvas } from './components/GraphCanvas';

interface RunMetadata {
  run_id: string;
  goal: string;
  created_at: string;
  is_success: number;
  parent_run_id?: string | null;
  forked_at_event_id?: string | null;
}

interface DBStats {
  sqlite: { runs: number; events: number };
  neo4j: { runs: number; events: number; entities: number; relationships: number };
  neo4j_available: boolean;
}

function App() {
  const [runs, setRuns] = useState<RunMetadata[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [stats, setStats] = useState<DBStats | null>(null);
  const [activeTab, setActiveTab] = useState<'episodic' | 'semantic'>('episodic');
  const [searchQuery, setSearchQuery] = useState('');
  const [graphData, setGraphData] = useState<{ nodes: any[]; links: any[] }>({ nodes: [], links: [] });
  const [selectedNode, setSelectedNode] = useState<any | null>(null);
  const [is3d, setIs3d] = useState(false);
  const [layoutMode, setLayoutMode] = useState<'force' | 'timeline'>('force');
  const [rightTab, setRightTab] = useState<'details' | 'raw'>('details');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Graph filters and rendering settings
  const [filters, setFilters] = useState({
    showEntities: true,
    showLLMCalls: true,
    showContainsLinks: false,
    showLabels: true,
    showParticles: true,
    glowEffects: true,
  });

  const selectedRunIdRef = useRef(selectedRunId);
  const activeTabRef = useRef(activeTab);

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId;
  }, [selectedRunId]);

  useEffect(() => {
    activeTabRef.current = activeTab;
  }, [activeTab]);

  // Setup real-time WebSocket ingestion subscription
  useEffect(() => {
    const wsUrl = window.location.port === '5173'
      ? 'ws://localhost:8000/api/ws'
      : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/ws`;

    let ws: WebSocket;
    let reconnectTimeout: any;

    const connect = () => {
      console.log('Connecting to sAG WebSocket:', wsUrl);
      ws = new WebSocket(wsUrl);

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          
          if (message.type === 'runs.created') {
            console.log('Live new runs created:', message.runs);
            setRuns((prev) => {
              const newRuns = message.runs.filter(
                (nr: any) => !prev.some((pr) => pr.run_id === nr.run_id)
              );
              return [...newRuns, ...prev];
            });
            setStats((prev: any) => {
              if (!prev) return prev;
              return {
                ...prev,
                sqlite: { ...prev.sqlite, runs: prev.sqlite.runs + message.runs.length }
              };
            });
          }

          if (message.type === 'events.appended') {
            console.log(`Live ${message.events.length} events appended.`);
            
            const activeRunId = selectedRunIdRef.current;
            const currentTab = activeTabRef.current;

            const hasActiveRunEvents = message.events.some((ev: any) => ev.run_id === activeRunId);
            const hasStatusChange = message.events.some(
              (ev: any) => ev.type === 'run.completed' || ev.type === 'run.failed' || ev.type === 'task.success'
            );

            if (hasActiveRunEvents || currentTab === 'semantic') {
              fetchGraphData();
            }

            if (hasStatusChange) {
              fetchMetadata();
            } else {
              setStats((prev: any) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  sqlite: { ...prev.sqlite, events: prev.sqlite.events + message.events.length }
                };
              });
            }
          }
        } catch (err) {
          console.error('Error handling WebSocket message:', err);
        }
      };

      ws.onclose = () => {
        console.warn('sAG WebSocket disconnected. Reconnecting in 3s...');
        reconnectTimeout = setTimeout(connect, 3000);
      };

      ws.onerror = (err) => {
        console.error('WebSocket connection error:', err);
        ws.close();
      };
    };

    connect();

    return () => {
      if (ws) ws.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
  }, []);

  // Fetch SQLite runs and DB counts
  const fetchMetadata = async () => {
    try {
      setError(null);
      const timestamp = Date.now();
      const [runsRes, statsRes] = await Promise.all([
        fetch(`/api/runs?_t=${timestamp}`),
        fetch(`/api/stats?_t=${timestamp}`)
      ]);

      if (!runsRes.ok || !statsRes.ok) {
        throw new Error('Failed to communicate with the sAG API server.');
      }

      const runsData = await runsRes.json();
      const statsData = await statsRes.json();

      setRuns(runsData);
      setStats(statsData);

      // Select first run by default if none selected and runs are available
      if (runsData.length > 0 && !selectedRunId) {
        setSelectedRunId(runsData[0].run_id);
      }
    } catch (err: any) {
      setError(err.message || 'Unknown network error.');
    }
  };

  // Fetch active graph data based on selected tab and run
  const fetchGraphData = async () => {
    const timestamp = Date.now();
    if (activeTab === 'semantic') {
      setLoading(true);
      try {
        const res = await fetch(`/api/semantic/graph?_t=${timestamp}`);
        if (!res.ok) throw new Error('Failed to retrieve semantic graph');
        const data = await res.json();
        setGraphData(data);
        setSelectedNode(null);
      } catch (err: any) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    } else if (activeTab === 'episodic' && selectedRunId) {
      setLoading(true);
      try {
        const res = await fetch(`/api/runs/${selectedRunId}/graph?_t=${timestamp}`);
        if (!res.ok) throw new Error('Failed to retrieve run trajectory graph');
        const data = await res.json();
        setGraphData(data);
        setSelectedNode(null);
      } catch (err: any) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
  };

  // Run metadata fetching on mount
  useEffect(() => {
    fetchMetadata();
  }, []);

  // Graph data refetching triggers
  useEffect(() => {
    fetchGraphData();
  }, [activeTab, selectedRunId]);

  // Handle manual dashboard reload
  const handleReload = () => {
    fetchMetadata();
    fetchGraphData();
  };

  // Filtered run list based on search bar
  const filteredRuns = useMemo(() => {
    return runs.filter(
      (run) =>
        run.run_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
        run.goal.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [runs, searchQuery]);

  // Active run details lookup
  const activeRunDetails = useMemo(() => {
    return runs.find((r) => r.run_id === selectedRunId) || null;
  }, [runs, selectedRunId]);

  // Node filtering logic to reduce layout clutter
  const filteredGraphData = useMemo(() => {
    if (!graphData || !Array.isArray(graphData.nodes) || !Array.isArray(graphData.links)) {
      return { nodes: [], links: [] };
    }

    const nodes = graphData.nodes.filter((node) => {
      if (!node) return false;
      if (node.group === 'Entity' && !filters.showEntities) return false;
      if (!filters.showLLMCalls && (node.type === 'llm.requested' || node.type === 'llm.responded')) return false;
      return true;
    });

    const nodeIds = new Set(nodes.map((n) => n.id));

    const links = graphData.links.filter((link) => {
      if (!link) return false;
      
      let sourceId = '';
      if (link.source) {
        if (typeof link.source === 'object') {
          sourceId = (link.source as any).id || '';
        } else {
          sourceId = String(link.source);
        }
      }
      
      let targetId = '';
      if (link.target) {
        if (typeof link.target === 'object') {
          targetId = (link.target as any).id || '';
        } else {
          targetId = String(link.target);
        }
      }
      
      if (!sourceId || !targetId) return false;
      if (link.type === 'CONTAINS' && !filters.showContainsLinks) return false;
      
      return nodeIds.has(sourceId) && nodeIds.has(targetId);
    });

    // Clone nodes and links to prevent D3 in-place mutation from causing layout freeze
    const clonedNodes = nodes.map((n) => ({ ...n }));
    const clonedLinks = links.map((l) => ({ ...l }));
    return { nodes: clonedNodes, links: clonedLinks };
  }, [graphData, filters]);

  // Navigate directly to a target run
  const handleRunJump = (runId: string) => {
    setSelectedRunId(runId);
    setActiveTab('episodic');
  };

  return (
    <div className="app-container">
      {/* 1. Header Bar */}
      <header className="dashboard-header">
        <div className="logo-section">
          <Activity className="logo-icon" />
          <div>
            <h1 className="logo-title">SemanticAgentGraph</h1>
            <div className="logo-subtitle">sAG Knowledge Hub & Trajectory Engine</div>
          </div>
        </div>

        <div className="header-actions">
          {/* Tab Selection */}
          <div className="btn-toggle-group">
            <button
              className={`btn-toggle ${activeTab === 'episodic' ? 'active' : ''}`}
              onClick={() => setActiveTab('episodic')}
            >
              <Network size={14} style={{ marginRight: '6px', verticalAlign: 'middle' }} />
              Episodic Trajectories
            </button>
            <button
              className={`btn-toggle ${activeTab === 'semantic' ? 'active' : ''}`}
              onClick={() => setActiveTab('semantic')}
            >
              <Globe size={14} style={{ marginRight: '6px', verticalAlign: 'middle' }} />
              Semantic Network
            </button>
          </div>

          {/* Database indicator */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px' }}>
            <Database size={14} color={stats?.neo4j_available ? '#4ade80' : '#f87171'} />
            <span style={{ color: stats?.neo4j_available ? '#81c784' : '#e57373', fontWeight: 'bold' }}>
              {stats?.neo4j_available ? 'Neo4j Online' : 'Neo4j Offline'}
            </span>
          </div>

          <button className="btn btn-secondary" onClick={handleReload} title="Refresh Database Projections">
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>
      </header>

      {/* 2. Left Sidebar Control & Run Selector */}
      <aside className="sidebar-panel">
        <div className="panel-header">
          <h2 className="panel-title">System Metrics</h2>
        </div>

        <div className="panel-content">
          {/* Counts widget */}
          <div className="stats-grid">
            <div className="stat-box">
              <div className="stat-value">{stats?.sqlite.runs || 0}</div>
              <div className="stat-label">SQLite Runs</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{stats?.sqlite.events || 0}</div>
              <div className="stat-label">SQLite Events</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{stats?.neo4j.entities || 0}</div>
              <div className="stat-label">Graph Entities</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{stats?.neo4j.relationships || 0}</div>
              <div className="stat-label">Graph Edges</div>
            </div>
          </div>

          {/* Settings Section */}
          <div className="glass-card control-group">
            <div className="control-label" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Sliders size={14} /> Graph Filters
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={filters.showEntities}
                onChange={(e) => setFilters({ ...filters, showEntities: e.target.checked })}
              />
              Show Semantic Entities
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={filters.showLLMCalls}
                onChange={(e) => setFilters({ ...filters, showLLMCalls: e.target.checked })}
              />
              Show LLM Request/Response Nodes
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={filters.showContainsLinks}
                onChange={(e) => setFilters({ ...filters, showContainsLinks: e.target.checked })}
              />
              Show Structural (Contains) Edges
            </label>
          </div>

          <div className="glass-card control-group">
            <div className="control-label" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Sliders size={14} /> Render Settings
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={filters.showLabels}
                onChange={(e) => setFilters({ ...filters, showLabels: e.target.checked })}
              />
              Show Text Labels
            </label>

            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={filters.showParticles}
                onChange={(e) => setFilters({ ...filters, showParticles: e.target.checked })}
              />
              Enable Link Particles
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={filters.glowEffects}
                onChange={(e) => setFilters({ ...filters, glowEffects: e.target.checked })}
              />
              Enable Selected Glow
            </label>
          </div>

          {/* Run Selection Section */}
          {activeTab === 'episodic' && (
            <div className="control-group" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
              <div className="control-label">Trajectories</div>
              <div className="search-box-container">
                <Search className="search-icon" />
                <input
                  type="text"
                  placeholder="Search Runs / Goals..."
                  className="text-input search-input-padding"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>

              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '8px',
                  marginTop: '10px',
                  maxHeight: 'calc(100vh - 460px)',
                  overflowY: 'auto',
                  paddingRight: '4px'
                }}
              >
                {filteredRuns.map((run) => (
                  <div
                    key={run.run_id}
                    className={`run-item ${selectedRunId === run.run_id ? 'selected' : ''}`}
                    onClick={() => setSelectedRunId(run.run_id)}
                  >
                    <div className="run-info">
                      <div className="run-id-title">{run.run_id.substring(0, 18)}...</div>
                      <div className="run-goal-desc">{run.goal}</div>
                    </div>
                    {run.is_success ? (
                      <span className="badge badge-success" style={{ padding: '2px 6px', fontSize: '9px' }}>Success</span>
                    ) : (
                      <span className="badge badge-failure" style={{ padding: '2px 6px', fontSize: '9px' }}>Fail</span>
                    )}
                  </div>
                ))}
                {filteredRuns.length === 0 && (
                  <div className="empty-state" style={{ padding: '20px' }}>
                    <Info className="empty-state-icon" size={24} />
                    <div style={{ fontSize: '12px' }}>No matching trajectories.</div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* 3. Center WebGL Force Graph Visualizer */}
      <main style={{ position: 'relative', width: '100%', height: '100%' }}>
        {loading && (
          <div
            style={{
              position: 'absolute',
              top: '20px',
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: 30,
              background: 'rgba(5, 6, 11, 0.85)',
              padding: '8px 16px',
              borderRadius: '20px',
              border: '1px solid var(--border-light)',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '13px'
            }}
          >
            <RefreshCw className="logo-icon" size={14} style={{ animation: 'spin 1.5s linear infinite' }} />
            Streaming graph projection...
          </div>
        )}

        {error && (
          <div
            style={{
              position: 'absolute',
              top: '20px',
              left: '50%',
              transform: 'translateX(-50%)',
              zIndex: 30,
              background: 'rgba(239, 68, 68, 0.95)',
              color: '#fff',
              padding: '10px 20px',
              borderRadius: '8px',
              maxWidth: '80%',
              fontSize: '13px'
            }}
          >
            {error}
          </div>
        )}

        <GraphCanvas
          data={filteredGraphData}
          is3d={is3d}
          onNodeSelect={setSelectedNode}
          selectedNodeId={selectedNode?.id}
          selectedRunId={selectedRunId}
          showLabels={filters.showLabels}
          showParticles={filters.showParticles}
          glowEffects={filters.glowEffects}
          layoutMode={layoutMode}
        />

        {/* 2D/3D & Layout Togglers */}
        <div className="canvas-controls" style={{ display: 'flex', gap: '10px' }}>
          <button className={`btn btn-secondary ${is3d ? 'active' : ''}`} onClick={() => setIs3d(!is3d)}>
            <Maximize2 size={14} />
            {is3d ? 'Toggle 2D View' : 'Toggle 3D View'}
          </button>
          <button 
            className={`btn btn-secondary ${layoutMode === 'timeline' ? 'active' : ''}`} 
            onClick={() => setLayoutMode(layoutMode === 'force' ? 'timeline' : 'force')}
          >
            <Activity size={14} />
            {layoutMode === 'timeline' ? 'Force Layout' : 'Timeline Layout'}
          </button>
        </div>
      </main>

      {/* 4. Right Sidebar details panel */}
      <aside className="sidebar-panel right">
        <div className="panel-header">
          <h2 className="panel-title">Inspector Panel</h2>
        </div>

        <div className="panel-content" style={{ overflowY: 'auto' }}>
          {selectedNode ? (
            <div className="details-section">
              <div className="details-title-row">
                <h3 style={{ fontSize: '18px', fontFamily: 'var(--font-title)', wordBreak: 'break-all' }}>
                  {selectedNode.label}
                </h3>
                <span
                  className={`badge badge-${
                    selectedNode.group === 'Run'
                      ? 'run'
                      : selectedNode.group === 'Entity'
                      ? 'entity'
                      : selectedNode.type === 'run.backtracked'
                      ? 'backtrack'
                      : selectedNode.type === 'run.failed' || selectedNode.type === 'task.failed'
                      ? 'failure'
                      : selectedNode.type === 'run.completed' || selectedNode.type === 'task.success'
                      ? 'success'
                      : 'agent'
                  }`}
                >
                  {selectedNode.group === 'Event' ? selectedNode.type : selectedNode.group}
                </span>
              </div>

              {/* Sidebar Tabs */}
              <div className="tab-control" style={{ display: 'flex', gap: '4px', borderBottom: '1px solid rgba(255,255,255,0.1)', marginBottom: '14px', marginTop: '8px' }}>
                <button
                  className={`tab-btn ${rightTab === 'details' ? 'active' : ''}`}
                  onClick={() => setRightTab('details')}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    padding: '8px 12px',
                    fontSize: '12px',
                    color: rightTab === 'details' ? '#38bdf8' : 'rgba(255,255,255,0.4)',
                    borderBottom: rightTab === 'details' ? '2px solid #38bdf8' : '2px solid transparent',
                    cursor: 'pointer',
                    fontWeight: 'bold',
                    transition: 'all 0.2s ease'
                  }}
                >
                  Overview & Details
                </button>
                <button
                  className={`tab-btn ${rightTab === 'raw' ? 'active' : ''}`}
                  onClick={() => setRightTab('raw')}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    padding: '8px 12px',
                    fontSize: '12px',
                    color: rightTab === 'raw' ? '#38bdf8' : 'rgba(255,255,255,0.4)',
                    borderBottom: rightTab === 'raw' ? '2px solid #38bdf8' : '2px solid transparent',
                    cursor: 'pointer',
                    fontWeight: 'bold',
                    transition: 'all 0.2s ease'
                  }}
                >
                  Raw JSON Payload
                </button>
              </div>

              {/* Formatted Details View */}
              {rightTab === 'details' && (
                <>
                  {/* Event specific statistics */}
                  {selectedNode.group === 'Event' && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', fontSize: '12px', marginBottom: '12px' }}>
                      <div className="stat-box" style={{ padding: '8px' }}>
                        <div style={{ color: 'var(--text-secondary)' }}>Sequence</div>
                        <div style={{ fontSize: '16px', fontWeight: 'bold' }}>{selectedNode.seq}</div>
                      </div>
                      <div className="stat-box" style={{ padding: '8px' }}>
                        <div style={{ color: 'var(--text-secondary)' }}>Actor</div>
                        <div style={{ fontSize: '14px', fontWeight: 'bold', color: 'var(--color-agent)' }}>
                          {selectedNode.actor || 'system'}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Detailed presentation based on Event contents */}
                  {selectedNode.group === 'Event' && selectedNode.payload && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                      {/* Backtracking & Negative feedback highlighting */}
                      {selectedNode.type === 'run.backtracked' && (
                        <div
                          className="glass-card"
                          style={{
                            background: 'rgba(251, 146, 60, 0.08)',
                            borderColor: 'var(--color-backtrack)',
                            color: 'var(--color-backtrack)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '8px'
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 'bold' }}>
                            <Undo2 size={16} /> Proactive Backtracking Triggered
                          </div>
                          <div style={{ fontSize: '13px' }}>
                            {selectedNode.payload.message || 'No explicit description provided.'}
                          </div>
                          {selectedNode.payload.feedback && (
                            <div
                              style={{
                                marginTop: '8px',
                                background: 'rgba(0,0,0,0.3)',
                                padding: '8px',
                                borderRadius: '4px',
                                borderLeft: '3px solid var(--color-backtrack)',
                                fontSize: '12px',
                                color: 'var(--text-primary)',
                                fontStyle: 'italic'
                              }}
                            >
                              <strong>Negative Feedback:</strong> {selectedNode.payload.feedback}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Actions / Terminal commands */}
                      {selectedNode.payload.action && (
                        <div className="control-group">
                          <div className="control-label" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <Terminal size={12} /> Executed Action
                          </div>
                          <div
                            className="payload-viewer"
                            style={{
                              background: '#02040a',
                              border: '1px solid #30363d',
                              color: '#39ff14', // Matrix green command text
                              maxHeight: '150px'
                            }}
                          >
                            $ {typeof selectedNode.payload.action === 'object'
                                ? JSON.stringify(selectedNode.payload.action, null, 2)
                                : String(selectedNode.payload.action)}
                          </div>
                        </div>
                      )}

                      {/* Agent Thoughts/Reasoning */}
                      {selectedNode.payload.thought && (
                        <div className="control-group">
                          <div className="control-label" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <Cpu size={12} /> Agent Reflection
                          </div>
                          <blockquote
                            style={{
                              borderLeft: '3px solid var(--color-agent)',
                              background: 'rgba(255, 255, 255, 0.02)',
                              padding: '10px 14px',
                              borderRadius: '0 8px 8px 0',
                              fontSize: '13px',
                              lineHeight: '1.4',
                              color: 'var(--text-secondary)',
                              fontStyle: 'italic'
                            }}
                          >
                            {typeof selectedNode.payload.thought === 'object'
                              ? JSON.stringify(selectedNode.payload.thought, null, 2)
                              : String(selectedNode.payload.thought)}
                          </blockquote>
                        </div>
                      )}

                      {/* Observation output */}
                      {selectedNode.payload.observation && (
                        <div className="control-group">
                          <div className="control-label" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <FileText size={12} /> Shell Observation
                          </div>
                          <div className="payload-viewer" style={{ maxHeight: '180px' }}>
                            {typeof selectedNode.payload.observation === 'object'
                              ? JSON.stringify(selectedNode.payload.observation, null, 2)
                              : String(selectedNode.payload.observation)}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Entity Node Attributes */}
                  {selectedNode.group === 'Entity' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                      <div className="glass-card">
                        <div className="control-label" style={{ marginBottom: '6px' }}>Entity Classification</div>
                        <div style={{ fontSize: '15px', fontWeight: '600', color: 'var(--color-entity)' }}>
                          {selectedNode.type}
                        </div>
                      </div>

                      {selectedNode.data && (
                        <div className="control-group">
                          <div className="control-label">Graph Metadata</div>
                          <pre className="payload-viewer">{JSON.stringify(selectedNode.data, null, 2)}</pre>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Run Node Attributes */}
                  {selectedNode.group === 'Run' && activeRunDetails && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                      <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        <div className="control-label">Goal Description</div>
                        <div style={{ fontSize: '14px', lineHeight: '1.4' }}>{activeRunDetails.goal}</div>
                      </div>

                      {/* Lineage links */}
                      {activeRunDetails.parent_run_id && (
                        <div
                          className="glass-card"
                          style={{
                            background: 'rgba(251, 146, 60, 0.05)',
                            borderColor: 'rgba(251, 146, 60, 0.2)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '6px'
                          }}
                        >
                          <div className="control-label" style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <GitFork size={12} /> Parent Branch Lineage
                          </div>
                          <div style={{ fontSize: '12px' }}>
                            Forked from run{' '}
                            <span
                              style={{
                                fontFamily: 'var(--font-mono)',
                                color: 'var(--color-backtrack)',
                                textDecoration: 'underline',
                                cursor: 'pointer'
                              }}
                              onClick={() => handleRunJump(activeRunDetails.parent_run_id!)}
                            >
                              {activeRunDetails.parent_run_id.substring(0, 12)}...
                            </span>
                          </div>
                        </div>
                      )}

                      <div className="glass-card" style={{ fontSize: '12px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <div>
                          <span style={{ color: 'var(--text-secondary)' }}>Created:</span>{' '}
                          {new Date(activeRunDetails.created_at).toLocaleString()}
                        </div>
                        <div>
                          <span style={{ color: 'var(--text-secondary)' }}>Execution Status:</span>{' '}
                          {activeRunDetails.is_success ? (
                            <span style={{ color: 'var(--color-success)', fontWeight: 'bold' }}>COMPLETED SUCCESS</span>
                          ) : (
                            <span style={{ color: 'var(--color-failure)', fontWeight: 'bold' }}>TERMINATED FAIL</span>
                          )}
                        </div>
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* Raw JSON Payload View */}
              {rightTab === 'raw' && (
                <div className="control-group">
                  <div className="control-label">Full Node Data Schema</div>
                  <pre className="payload-viewer" style={{ maxHeight: '480px', fontSize: '11px', fontFamily: 'Consolas, monospace', lineHeight: '1.4' }}>
                    {JSON.stringify(selectedNode, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ) : (
            <div className="empty-state" style={{ height: '250px' }}>
              <Maximize2 className="empty-state-icon" size={32} />
              <div style={{ fontSize: '14px', fontWeight: 'bold', color: 'var(--text-primary)' }}>
                No Node Selected
              </div>
              <div style={{ fontSize: '12px', maxWidth: '80%' }}>
                Select or click any Event circle or Entity square on the WebGL canvas to inspect details, logs, errors, and backtracking points.
              </div>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

export default App;
