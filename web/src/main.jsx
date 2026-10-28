import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { applyTheme, getTheme } from './theme'

// ?theme=dark|light deep-links a theme (handy for screenshots and for agents)
const forced = new URLSearchParams(window.location.search).get('theme')
if (forced === 'dark' || forced === 'light') localStorage.setItem('huhq-theme', forced)
applyTheme(getTheme())

createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>)
