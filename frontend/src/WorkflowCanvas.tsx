import { useEffect, useMemo, useRef, useState } from 'react';
import { Background, BackgroundVariant, Controls, Handle, MarkerType, Position, ReactFlow, type Node, type NodeProps, type ReactFlowInstance } from '@xyflow/react';
import { Button, ButtonGroup, List, ListItemButton, Typography, useMediaQuery } from '@mui/material';
import CenterFocusStrong from '@mui/icons-material/CenterFocusStrong';
import HubOutlined from '@mui/icons-material/HubOutlined';
import ViewListOutlined from '@mui/icons-material/ViewListOutlined';
import { statusIcons, statusLabels, StatusChip } from './Status';
import { terminal, type Run, type Status, type Workflow } from './types';
import { colors } from './theme';

type FlowNode = Node<{ label: string; human: boolean; status: Status; count: number; index: number }, 'workflow'>;
function WorkflowNode({ data, selected }: NodeProps<FlowNode>) {
  const Icon = statusIcons[data.status];
  return <div className={`workflow-node node-${data.status} ${selected ? 'node-selected' : ''}`}>
    <Handle type="target" position={Position.Top} />
    <div className="node-line"><span className="node-index">{String(data.index).padStart(2, '0')}</span><span className="node-label">{data.label}</span><Icon fontSize="small" /></div>
    <div className="node-meta"><span>{statusLabels[data.status]}</span><span>{data.count ? `第 ${data.count} 次` : data.human ? '人工步骤' : '自动步骤'}</span></div>
    {data.status === 'waiting' && <div className="node-hint">点击处理待办</div>}
    <Handle type="source" position={Position.Bottom} />
  </div>;
}
const nodeTypes = { workflow: WorkflowNode };

export default function WorkflowCanvas({ workflow, run, selected, onSelect }: {
  workflow: Workflow; run: Run | null; selected: string | null; onSelect: (id: string) => void;
}) {
  const compact = useMediaQuery('(max-width: 700px)');
  const [view, setView] = useState<'graph' | 'list'>(compact ? 'list' : 'graph');
  const [instance, setInstance] = useState<ReactFlowInstance<FlowNode> | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  useEffect(() => {
    if (!instance || view !== 'graph' || !surface.current) return;
    let frame = 0;
    let previousWidth = 0, previousHeight = 0;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width === previousWidth && height === previousHeight) return;
      previousWidth = width; previousHeight = height;
      cancelAnimationFrame(frame);
      // hidden 的辅助区初始化时没有尺寸，展开或调整后按真实画布重新适配。
      if (width > 0 && height > 0) frame = requestAnimationFrame(() => {
        frame = requestAnimationFrame(() => { void instance.fitView({ padding: 0.16, maxZoom: 0.9, duration: 0 }); });
      });
    });
    observer.observe(surface.current);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [instance, view, workflow.id]);
  const nodes = useMemo<FlowNode[]>(() => workflow.nodes.map((node, index) => {
    const executed = run?.executions.filter(item => item.node === node.id) ?? [];
    const status = executed.at(-1)?.status ?? (run && terminal(run.status) ? 'skipped' : 'idle');
    return { id: node.id, position: node.position, type: 'workflow', selected: selected === node.id,
      data: { label: node.label, human: node.human, status, count: executed.length, index: index + 1 },
      ariaLabel: `${node.label}，${statusLabels[status]}，执行 ${executed.length} 次，按回车查看详情` };
  }), [workflow.nodes, run, selected]);
  const edges = useMemo(() => workflow.edges.map(edge => {
    const count = run?.traversed_edges.filter(item => item.source === edge.source && item.target === edge.target).length ?? 0;
    const backwards = workflow.nodes.findIndex(x => x.id === edge.target) <= workflow.nodes.findIndex(x => x.id === edge.source);
    return { ...edge, type: backwards ? 'smoothstep' : 'default', zIndex: count ? 1 : 0,
      style: { stroke: count ? colors.ink : colors.line, strokeWidth: count ? 2.3 : 1, opacity: count ? 1 : 0.65 },
      markerEnd: { type: MarkerType.ArrowClosed, color: count ? colors.ink : colors.line, width: 16, height: 16 },
      label: count > 1 ? `×${count}` : '', labelStyle: { fill: colors.body, fontSize: 11 }, animated: false, focusable: false };
  }), [workflow, run]);
  function focusCurrent() {
    if (!run?.current_node) return;
    onSelect(run.current_node);
    void instance?.fitView({ nodes: [{ id: run.current_node }], duration: reduceMotion ? 0 : 240, maxZoom: 1.1, padding: 0.8 });
  }
  return <section className="flow-region" aria-label="工作流">
    <div className="flow-toolbar">
      <div><Typography variant="subtitle2">处理流程</Typography><Typography variant="caption" color="text.secondary">真实节点 · 点击查看执行详情</Typography></div>
      <div className="toolbar-actions">
        <Button size="small" startIcon={<CenterFocusStrong />} disabled={!run?.current_node} onClick={focusCurrent}>当前节点</Button>
        <ButtonGroup size="small" aria-label="流程视图">
          <Button aria-label="图形视图" aria-pressed={view === 'graph'} variant={view === 'graph' ? 'contained' : 'outlined'} onClick={() => setView('graph')}><HubOutlined fontSize="small" /></Button>
          <Button aria-label="列表视图" aria-pressed={view === 'list'} variant={view === 'list' ? 'contained' : 'outlined'} onClick={() => setView('list')}><ViewListOutlined fontSize="small" /></Button>
        </ButtonGroup>
      </div>
    </div>
    <div className="flow-surface" ref={surface}>
      {view === 'graph' ? <ReactFlow<FlowNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} onInit={setInstance}
        nodesDraggable={false} nodesConnectable={false} edgesFocusable={false} elementsSelectable
        onNodeClick={(_, node) => onSelect(node.id)}
        onKeyDown={event => {
          const element = (event.target as HTMLElement).closest<HTMLElement>('.react-flow__node');
          if (element?.dataset.id && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault(); onSelect(element.dataset.id);
          }
        }}
        fitView fitViewOptions={{ padding: 0.16, maxZoom: 0.9 }} minZoom={0.3} maxZoom={1.5}
        ariaLabelConfig={{ 'controls.zoomIn.ariaLabel': '放大流程', 'controls.zoomOut.ariaLabel': '缩小流程', 'controls.fitView.ariaLabel': '适应画布',
          'node.a11yDescription.default': '按回车查看节点详情，方向键平移画布。' }}>
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color={colors.line} />
        <Controls showInteractive={false} />
      </ReactFlow> : <List className="node-list" aria-label="节点列表">{nodes.map(node => <ListItemButton key={node.id} selected={selected === node.id} onClick={() => onSelect(node.id)}>
        <span className="node-list-number">{String(node.data.index).padStart(2, '0')}</span><div className="grow"><Typography variant="body2" fontWeight={600}>{node.data.label}</Typography><Typography variant="caption" color="text.secondary">执行 {node.data.count} 次</Typography></div><StatusChip status={node.data.status} />
      </ListItemButton>)}</List>}
    </div>
    <div className="flow-legend"><span><i className="legend-line" />已经过的连线</span><span>支持缩放与平移</span><span>不显示估算进度</span></div>
  </section>;
}
