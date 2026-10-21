import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Loopback only: the dev server is never reachable from the network.
    host: '127.0.0.1',
    port: 3000,
  },
  build: {
    outDir: 'dist',
    sourcemap: false,   // the public site ships no source maps
  },
});
