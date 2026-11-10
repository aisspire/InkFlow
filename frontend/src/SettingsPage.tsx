import { useEffect, useState } from 'react';
import { Alert, Button, CircularProgress, FormControlLabel, Switch, TextField, Typography } from '@mui/material';
import KeyOutlined from '@mui/icons-material/KeyOutlined';
import SaveOutlined from '@mui/icons-material/SaveOutlined';
import SettingsOutlined from '@mui/icons-material/SettingsOutlined';
import { api } from './api';
import type { Settings } from './types';
import DirectoryField from './DirectoryField';

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  async function load() {
    setError('');
    try { setSettings(await api<Settings>('/settings')); } catch (err) { setError((err as Error).message); }
  }
  useEffect(() => { void load(); }, []);
  async function save() {
    if (!settings) return;
    setBusy(true); setError(''); setSuccess('');
    try {
      const { llm, blog, review, report } = settings;
      setSettings(await api<Settings>('/settings', { method: 'PUT', body: JSON.stringify({ llm, blog, review, report }) }));
      setSuccess('设置已保存，仅对后续创建的任务生效。');
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function saveKey(clear: boolean) {
    setBusy(true); setError(''); setSuccess('');
    try {
      const result = await api<Pick<Settings, 'api_key_configured' | 'local_key_saved'>>('/settings/api-key', { method: clear ? 'DELETE' : 'PUT', ...(clear ? {} : { body: JSON.stringify({ api_key: key }) }) });
      setSettings(current => current ? { ...current, ...result } : current); setKey('');
      setSuccess(clear ? '本地密钥已清除；如环境变量已配置，将继续使用环境变量。' : '本地密钥已保存，仅影响后续任务。');
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <main className="page-scroll"><div className="settings-page">
    <div className="page-heading"><SettingsOutlined /><div><Typography variant="h5">设置</Typography><Typography color="text.secondary" variant="body2">管理模型和本地输出目录。运行中的任务保留原有配置。</Typography></div></div>
    {error && <Alert severity="error" action={!settings && <Button onClick={() => void load()}>重试</Button>}>{error}</Alert>}
    {success && <Alert severity="success" role="status">{success}</Alert>}
    {!settings && !error && <CircularProgress aria-label="正在读取设置" />}
    {settings && <>
      <section className="settings-section"><div className="section-intro"><Typography variant="h6">模型服务</Typography><FormControlLabel control={<Switch checked={settings.llm.enabled} onChange={e => setSettings({ ...settings, llm: { ...settings.llm, enabled: e.target.checked } })} />} label="启用真实模型" /></div>
        {!settings.llm.enabled && <Alert severity="info" sx={{ mb: 3 }}>关闭后仍可编辑本地文章，全文检查不可用；任务流程使用本地占位结果。</Alert>}
        <div className="fields-grid">
          <TextField label="模型名称" value={settings.llm.model} onChange={e => setSettings({ ...settings, llm: { ...settings.llm, model: e.target.value } })} />
          <TextField label="接口类型" value="OpenAI-compatible" slotProps={{ input: { readOnly: true } }} />
          <TextField className="span-two" label="服务地址（base_url）" placeholder="https://your-provider.example/v1" value={settings.llm.base_url} helperText="留空使用 SDK 默认地址；不要在地址中填写密钥" onChange={e => setSettings({ ...settings, llm: { ...settings.llm, base_url: e.target.value } })} />
          <TextField type="number" label="生成随机性（temperature）" value={settings.llm.temperature} slotProps={{ htmlInput: { min: 0, max: 2, step: 0.1 } }} onChange={e => setSettings({ ...settings, llm: { ...settings.llm, temperature: Number(e.target.value) } })} />
          <TextField type="number" label="请求超时（秒）" value={settings.llm.timeout_seconds} slotProps={{ htmlInput: { min: 1, max: 600 } }} onChange={e => setSettings({ ...settings, llm: { ...settings.llm, timeout_seconds: Number(e.target.value) } })} />
          <TextField type="number" label="最大输出 token" value={settings.llm.max_tokens} helperText="0 表示交给模型服务决定" slotProps={{ htmlInput: { min: 0, max: 1000000 } }} onChange={e => setSettings({ ...settings, llm: { ...settings.llm, max_tokens: Number(e.target.value) } })} />
          <TextField label="密钥环境变量名" value={settings.llm.api_key_env} onChange={e => setSettings({ ...settings, llm: { ...settings.llm, api_key_env: e.target.value } })} />
        </div>
      </section>
      <section className="settings-section"><Typography variant="h6">本地目录</Typography><Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>后端：{settings.platform === 'nt' ? 'Windows' : 'Linux / WSL'}。相对路径基于 {settings.config_directory}。</Typography>
        <div className="fields-grid">
          <DirectoryField className="span-two" label="博客仓库路径" purpose="blog_repository" value={settings.blog.repo_path} helperText="可留空；填入后需要是已存在的本机目录" disabled={busy || picking} onPicking={setPicking} onError={setError} onChange={path => setSettings(old => old && ({ ...old, blog: { ...old.blog, repo_path: path } }))} />
          <DirectoryField className="span-two" label="博客内容目录" purpose="blog_content" repoPath={settings.blog.repo_path} value={settings.blog.content_dir} helperText="仓库内的相对路径，最终只写入 <slug>/index.mdx" disabled={busy || picking} onPicking={setPicking} onError={setError} onChange={path => setSettings(old => old && ({ ...old, blog: { ...old.blog, content_dir: path } }))} />
          <DirectoryField label="审阅稿目录" purpose="review" value={settings.review.dir} disabled={busy || picking} onPicking={setPicking} onError={setError} onChange={path => setSettings(old => old && ({ ...old, review: { dir: path } }))} />
          <DirectoryField label="报告目录" purpose="report" value={settings.report.dir} disabled={busy || picking} onPicking={setPicking} onError={setError} onChange={path => setSettings(old => old && ({ ...old, report: { dir: path } }))} />
        </div>
      </section>
      <div className="settings-save"><Button variant="contained" startIcon={<SaveOutlined />} disabled={busy || picking} onClick={() => void save()}>保存设置</Button></div>
      <section className="settings-section"><div className="section-intro"><Typography variant="h6"><KeyOutlined fontSize="small" /> API Key</Typography><Typography variant="body2" color={settings.api_key_configured ? 'success.main' : 'text.secondary'}>{settings.api_key_configured ? '已配置' : '未配置'}</Typography></div>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>密钥单独保存在本机私有文件中，页面不会读取密钥原文。本地密钥优先于环境变量。</Typography>
        <TextField label="替换为新的 API Key" type="password" value={key} autoComplete="new-password" onChange={e => setKey(e.target.value)} />
        <div className="button-row"><Button variant="outlined" disabled={busy || !key.trim()} onClick={() => void saveKey(false)}>保存新密钥</Button><Button color="error" disabled={busy || !settings.local_key_saved} onClick={() => void saveKey(true)}>清除本地密钥</Button></div>
      </section>
    </>}
  </div></main>;
}
