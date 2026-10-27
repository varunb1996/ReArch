import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/alegreya'
import '@fontsource-variable/alegreya/wght-italic.css'
import '@fontsource-variable/sofia-sans-condensed'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
