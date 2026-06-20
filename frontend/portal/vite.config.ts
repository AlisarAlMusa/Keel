import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
  },
  // Expose VITE_* env vars to the React app so branding can be parameterized
  // per portal instance without forking code (spec §S9, D-P5-004).
  envPrefix: 'VITE_',
})
