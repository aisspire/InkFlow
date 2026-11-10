let workspace = '';
export function setWorkspace(value: string) { workspace = value; }

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(workspace ? { 'X-InkFlow-Workspace': encodeURIComponent(workspace) } : {}), ...options.headers },
    });
  } catch {
    throw new Error('无法连接本机服务，请确认后端正在运行并等待连接恢复。');
  }
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(typeof value.detail === 'string' ? value.detail : `请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}
export type PickerPurpose = 'article' | 'workspace' | 'blog_repository' | 'blog_content' | 'review' | 'report';
export async function chooseLocalPath(purpose: PickerPurpose, initial_path = '', repo_path = '') {
  return (await api<{ path: string | null }>('/local-picker', { method: 'POST', body: JSON.stringify({ purpose, initial_path, repo_path }) })).path;
}
export const requestId = () => crypto.randomUUID();
export const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false });
