import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { get } from './api'

const Ctx = createContext(null)
export const useStore = () => useContext(Ctx)

export function StoreProvider({ children }) {
  const [state, setState] = useState({ channels: [], providers: null, settings: null, library: {}, me: null, ready: false })
  const reload = useCallback(async () => {
    const [channels, providers, settings, hashtag_groups, templates, saved_replies, tags, status] = await Promise.all([
      get('/api/channels'), get('/api/providers'), get('/api/settings'),
      get('/api/library/hashtag_groups'), get('/api/library/templates'), get('/api/library/saved_replies'), get('/api/library/tags'),
      get('/api/auth/status'),
    ])
    setState({ channels, providers, settings, library: { hashtag_groups, templates, saved_replies, tags }, me: status.user || null, ready: true })
  }, [])
  useEffect(() => { reload().catch(() => {}) }, [reload])
  const connector = (id) => state.providers?.connectors.find((c) => c.id === id)
  // what the signed-in teammate may do (the server enforces it; the UI just hides what would be refused)
  const can = state.me?.can || { manage_team: true, approve: true, publish: true, write: true, settings: true, comment: true, needs_approval: false }
  return <Ctx.Provider value={{ ...state, reload, connector, can }}>{children}</Ctx.Provider>
}
