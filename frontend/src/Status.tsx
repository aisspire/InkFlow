import { Chip } from '@mui/material';
import CheckCircleOutline from '@mui/icons-material/CheckCircleOutline';
import ErrorOutline from '@mui/icons-material/ErrorOutline';
import HourglassEmpty from '@mui/icons-material/HourglassEmpty';
import PauseCircleOutline from '@mui/icons-material/PauseCircleOutline';
import PendingOutlined from '@mui/icons-material/PendingOutlined';
import PlayCircleOutline from '@mui/icons-material/PlayCircleOutline';
import RemoveCircleOutline from '@mui/icons-material/RemoveCircleOutline';
import StopCircleOutlined from '@mui/icons-material/StopCircleOutlined';
import type { Status } from './types';
export const statusLabels: Record<Status, string> = {
  idle: '未执行', running: '执行中', waiting: '等待输入', completed: '已完成', failed: '失败',
  skipped: '已跳过', stopped: '已停止', stopping: '正在停止', interrupted: '已中断',
};
export const statusIcons = {
  idle: PendingOutlined, running: PlayCircleOutline, waiting: PauseCircleOutline, completed: CheckCircleOutline,
  failed: ErrorOutline, skipped: RemoveCircleOutline, stopped: StopCircleOutlined, stopping: HourglassEmpty, interrupted: ErrorOutline,
};
export function StatusChip({ status }: { status: Status }) {
  const Icon = statusIcons[status];
  return <Chip size="small" className={`status status-${status}`} icon={<Icon />} label={statusLabels[status]} />;
}
