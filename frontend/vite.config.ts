import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        advancedChunks: {
          groups: [
            // recharts has internal circular imports — splitting it across lazy
            // chunks breaks init order ("TypeError: t is not a function").
            // Force recharts + its d3 deps into a single shared chunk.
            { name: 'charts', test: /node_modules[\\/](recharts|d3-|victory-vendor|internmap|decimal\.js)/ },
            { name: 'react-vendor', test: /node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/ },
          ],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/__tests__/setup.ts'],
  },
})
