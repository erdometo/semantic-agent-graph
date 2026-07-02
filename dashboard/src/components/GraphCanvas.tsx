import React, { useRef, useEffect, useCallback, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import ForceGraph3D from 'react-force-graph-3d';
import * as THREE from 'three';

interface GraphNode {
  id: string;
  label: string;
  group: 'Run' | 'Event' | 'Entity';
  type?: string;
  seq?: number;
  timestamp?: string;
  payload?: any;
  actor?: string;
  data?: any;
  run_id?: string;
  fx?: number;
  fy?: number;
  x?: number;
  y?: number;
  z?: number;
}

interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
  type: string;
  data?: any;
}

interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

interface GraphCanvasProps {
  data: GraphData;
  is3d: boolean;
  onNodeSelect: (node: GraphNode) => void;
  selectedNodeId?: string | null;
  selectedRunId?: string | null;
  showLabels?: boolean;
  showParticles?: boolean;
  glowEffects?: boolean;
  layoutMode?: 'force' | 'timeline';
}

// Color and link accessor helpers defined outside to maintain constant reference
const getNodeColor = (node: GraphNode) => {
  if (node.group === 'Run') return '#e2e8f0';
  if (node.group === 'Entity') return '#c084fc';
  
  // Event node types
  const type = node.type || '';
  if (type === 'run.failed' || type === 'task.failed') return '#f87171';
  if (type === 'run.completed' || type === 'task.success') return '#4ade80';
  if (type === 'run.backtracked' || type.includes('backtrack')) return '#fb923c';
  if (type.includes('fail') || type.includes('error')) return '#f87171';
  
  return '#38bdf8'; // Default agent step/event
};

const getLinkColor = (link: GraphLink) => {
  switch (link.type) {
    case 'NEXT':
      return 'rgba(56, 189, 248, 0.4)'; // Cyan
    case 'FORKED_FROM':
      return 'rgba(251, 146, 60, 0.6)'; // Orange
    case 'PROCESSED':
      return 'rgba(192, 132, 252, 0.4)'; // Purple
    case 'CAUSED_BY':
      return 'rgba(248, 113, 113, 0.5)'; // Red
    default:
      return 'rgba(255, 255, 255, 0.15)'; // Grey
  }
};

export const GraphCanvas: React.FC<GraphCanvasProps> = ({
  data,
  is3d,
  onNodeSelect,
  selectedNodeId,
  selectedRunId,
  showLabels = true,
  showParticles = true,
  glowEffects = true,
  layoutMode = 'force',
}) => {
  const fg2dRef = useRef<any>(null);
  const fg3dRef = useRef<any>(null);

  // Keep all reactive visual settings in a single ref to prevent drawing function recreation
  const settingsRef = useRef({
    showLabels,
    showParticles,
    glowEffects,
    selectedNodeId
  });

  // Synchronously update the ref on every render before the child canvas processes frames
  settingsRef.current = {
    showLabels,
    showParticles,
    glowEffects,
    selectedNodeId
  };

  // 2D Node Canvas drawing - Memoized with empty dependency array to keep reference stable
  const drawNode2D = useCallback((node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const { showLabels, glowEffects, selectedNodeId } = settingsRef.current;
    const isSelected = selectedNodeId && node.id === selectedNodeId;
    const color = getNodeColor(node);
    
    // Determine size
    let size = 6;
    if (node.group === 'Run') size = 10;
    if (node.group === 'Entity') size = 8;
    if (isSelected) size += 2;

    const x = node.x || 0;
    const y = node.y || 0;

    ctx.save();
    
    // Setup shadow/glow (only for selected nodes to prevent performance degradation, if enabled)
    if (glowEffects && isSelected) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 12;
    } else {
      ctx.shadowBlur = 0;
    }
    ctx.fillStyle = color;
    
    if (node.group === 'Entity') {
      // Draw square for Entity
      ctx.fillRect(x - size, y - size, size * 2, size * 2);
      
      // Outline border
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = isSelected ? 2 : 0.5;
      ctx.strokeRect(x - size, y - size, size * 2, size * 2);
    } else {
      // Draw circle for Run/Event
      ctx.beginPath();
      ctx.arc(x, y, size, 0, 2 * Math.PI, false);
      ctx.fill();

      // Outline border
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = isSelected ? 2 : 0.5;
      ctx.stroke();
    }



    // Draw label text below node if zoomed in and enabled
    if (showLabels && globalScale > 1.2) {
      const label = String(node.label || node.id || '');
      const truncatedLabel = label.length > 25 ? `${label.substring(0, 22)}...` : label;
      ctx.shadowBlur = 0; // Remove text glow for readability
      ctx.font = '10px Inter, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle = isSelected ? '#ffffff' : 'rgba(248, 250, 252, 0.75)';
      ctx.fillText(truncatedLabel, x, y + size + 3);
    }

    ctx.restore();
  }, []);

  // 3D Node Mesh creator - Memoized depending on selectedNodeId
  const createNode3D = useCallback((node: GraphNode) => {
    const color = getNodeColor(node);
    const isSelected = selectedNodeId && node.id === selectedNodeId;
    let size = 4;
    if (node.group === 'Run') size = 7;
    if (node.group === 'Entity') size = 6;
    if (isSelected) size += 1.5;

    let geometry;
    if (node.group === 'Entity') {
      geometry = new THREE.BoxGeometry(size, size, size);
    } else {
      geometry = new THREE.SphereGeometry(size, 16, 16);
    }

    const material = new THREE.MeshLambertMaterial({
      color: new THREE.Color(color),
      transparent: true,
      opacity: 0.9,
    });

    const mesh = new THREE.Mesh(geometry, material);

    if (isSelected) {
      const wireframeGeometry = new THREE.EdgesGeometry(geometry);
      const wireframeMaterial = new THREE.LineBasicMaterial({ color: 0xffffff, linewidth: 2 });
      const wireframe = new THREE.LineSegments(wireframeGeometry, wireframeMaterial);
      mesh.add(wireframe);
    }

    return mesh;
  }, [selectedNodeId]);

  // Handle node selection - Memoized
  const handleNodeClick = useCallback((node: any) => {
    onNodeSelect(node as GraphNode);
  }, [onNodeSelect]);

  // Memoized link accessors
  const getLinkColorCallback = useCallback((link: any) => getLinkColor(link), []);
  const getLinkWidthCallback = useCallback((link: any) => {
    return link.type === 'NEXT' || link.type === 'FORKED_FROM' ? 2.5 : 1;
  }, []);
  const linkDirectionalParticlesCallback = useCallback((link: any) => {
    return settingsRef.current.showParticles && (link.type === 'NEXT' || link.type === 'FORKED_FROM') ? 4 : 0;
  }, []);

  // Process data for layout modes (Timeline layout locks fx/fy coordinates)
  const processedData = useMemo(() => {
    if (!data || !data.nodes) return { nodes: [], links: [] };

    // Create shallow copies of nodes and links
    const nodes = data.nodes.map(node => ({ ...node }));
    const links = data.links.map(link => ({ ...link }));

    if (layoutMode === 'timeline') {
      // Find all unique run_ids present to map them into vertical tracks
      const runIds = Array.from(new Set(
        nodes
          .filter(n => n.group === 'Event')
          .map(n => n.run_id)
          .filter(Boolean)
      ));

      // 1. Assign fixed coordinates to runs and event sequence nodes
      nodes.forEach(node => {
        if (node.group === 'Run') {
          node.fx = -120;
          node.fy = 0;
          node.z = 0;
        } else if (node.group === 'Event') {
          // X locks to sequence index
          const seq = node.seq || 0;
          node.fx = seq * 80;

          // Y locks to distinct run ID vertical lanes
          const runIdx = runIds.indexOf(node.run_id);
          node.fy = runIdx === -1 ? 0 : runIdx * 120;
          node.z = 0;
        }
      });

      // 2. Assign average fixed X positions to Entity nodes and place them on top lane
      nodes.forEach(node => {
        if (node.group === 'Entity') {
          // Find links touching this Entity node
          const connectedLinks = links.filter(l => {
            const srcId = typeof l.source === 'object' ? (l.source as any).id : l.source;
            const tgtId = typeof l.target === 'object' ? (l.target as any).id : l.target;
            return srcId === node.id || tgtId === node.id;
          });

          // Extract partner Event nodes sequences
          const partnerEventSeqs = connectedLinks.map(l => {
            const srcId = typeof l.source === 'object' ? (l.source as any).id : l.source;
            const tgtId = typeof l.target === 'object' ? (l.target as any).id : l.target;
            const partnerId = srcId === node.id ? tgtId : srcId;
            const partnerNode = nodes.find(n => n.id === partnerId);
            return partnerNode && partnerNode.group === 'Event' ? (partnerNode.seq || 0) : null;
          }).filter((s): s is number => s !== null);

          if (partnerEventSeqs.length > 0) {
            const avgSeq = partnerEventSeqs.reduce((a, b) => a + b, 0) / partnerEventSeqs.length;
            node.fx = avgSeq * 80;
          } else {
            node.fx = 150;
          }

          // Fixed Entity lane on top to prevent text clashes
          node.fy = -150;
          node.z = 0;
        }
      });
    } else {
      // Clear fixed coordinate coordinates so d3-force simulation flows freely
      nodes.forEach(node => {
        node.fx = undefined;
        node.fy = undefined;
      });
    }

    return { nodes, links };
  }, [data, layoutMode]);

  useEffect(() => {
    if (is3d) {
      setTimeout(() => {
        if (fg3dRef.current) fg3dRef.current.zoomToFit(400, 50);
      }, 100);
    } else {
      setTimeout(() => {
        if (fg2dRef.current) {
          fg2dRef.current.zoomToFit(400, 50);
          fg2dRef.current.d3ReheatSimulation();
        }
      }, 100);
    }
  }, [processedData, is3d]);



  const componentKey = `${selectedRunId || 'default'}-${is3d ? '3d' : '2d'}`;

  return (
    <div className="canvas-container">
      {is3d ? (
        <ForceGraph3D
          key={componentKey}
          ref={fg3dRef}
          graphData={processedData}
          nodeThreeObject={createNode3D}
          onNodeClick={handleNodeClick}
          linkColor={getLinkColorCallback}
          linkWidth={getLinkWidthCallback}
          linkDirectionalParticles={linkDirectionalParticlesCallback}
          linkDirectionalParticleWidth={2}
          linkDirectionalParticleSpeed={0.005}
          backgroundColor="#05060b"
          controlType="trackball"
        />
      ) : (
        <ForceGraph2D
          key={componentKey}
          ref={fg2dRef}
          graphData={processedData}
          nodeCanvasObject={drawNode2D}
          onNodeClick={handleNodeClick}
          linkColor={getLinkColorCallback}
          linkWidth={getLinkWidthCallback}
          linkDirectionalParticles={linkDirectionalParticlesCallback}
          linkDirectionalParticleWidth={3}
          linkDirectionalParticleSpeed={0.006}
          enableNodeDrag={true}
        />
      )}
    </div>
  );
};
