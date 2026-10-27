import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// GitHub Pages serves a project site from /<repo>/, so CI sets VITE_BASE=/ReArch/.
export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [react()],
})
