import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: loadEnv(mode, '.', 'INKFLOW_BACKEND_').INKFLOW_BACKEND_URL || 'http://127.0.0.1:18765',
        changeOrigin: false,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          flow: ['@xyflow/react'],
          markdown: ['react-markdown', 'remark-gfm'],
          material: ['@mui/material', '@emotion/react', '@emotion/styled'],
        },
      },
    },
  },
}));
