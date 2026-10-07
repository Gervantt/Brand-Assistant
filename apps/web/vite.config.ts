import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  // Mantine + react-markdown make the main chunk ~230 kB gzipped; acceptable for an app shell.
  build: { chunkSizeWarningLimit: 800 },
});
