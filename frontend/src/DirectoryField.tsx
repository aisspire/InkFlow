import { useState } from 'react';
import { Button, InputAdornment, TextField } from '@mui/material';
import FolderOpenOutlined from '@mui/icons-material/FolderOpenOutlined';
import { chooseLocalPath, type PickerPurpose } from './api';

export default function DirectoryField({ label, value, purpose, repoPath, helperText, className, disabled, onChange, onError, onPicking }: {
  label: string; value: string; purpose: Exclude<PickerPurpose, 'article'>; repoPath?: string;
  helperText?: string; className?: string; disabled?: boolean; onChange: (path: string) => void;
  onError: (message: string) => void; onPicking?: (picking: boolean) => void;
}) {
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState('');
  async function browse() {
    setPicking(true); setError(''); onPicking?.(true); onError('');
    try {
      const path = await chooseLocalPath(purpose, value, repoPath);
      if (path !== null) onChange(path);
    } catch (error) { setError((error as Error).message); }
    finally { setPicking(false); onPicking?.(false); }
  }
  return <TextField fullWidth className={className} label={label} value={value} error={!!error} helperText={error || helperText}
    disabled={disabled || picking} onChange={event => { setError(''); onChange(event.target.value); }}
    slotProps={{ formHelperText: { role: error ? 'alert' : undefined }, input: { endAdornment: <InputAdornment position="end"><Button size="small" startIcon={<FolderOpenOutlined />}
      aria-label={`浏览${label}`} disabled={disabled || picking} onClick={() => void browse()}>{picking ? '选择中…' : '浏览…'}</Button></InputAdornment> } }} />;
}
