import React from 'react';
import ReactDOM from 'react-dom/client';
import { CssBaseline, ThemeProvider } from '@mui/material';
import EditorWorkbench from './EditorWorkbench';
import { theme } from './theme';
import '@xyflow/react/dist/style.css';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><ThemeProvider theme={theme}><CssBaseline /><EditorWorkbench /></ThemeProvider></React.StrictMode>,
);
