import { useCallback, useEffect, useRef, useState } from 'react'
import { api, post } from '../api'

const emptyRuntime = {
  feature: 'IDLE',
  phase: 'IDLE',
  generation: 0,
  active_components: [],
  message: '',
}

export function useConsole() {
  const [features, setFeatures] = useState([])
  const [maps, setMaps] = useState([])
  const [activeMap, setActiveMap] = useState('')
  const [environment, setEnvironment] = useState('unknown')
  const [runtime, setRuntime] = useState(emptyRuntime)
  const [health, setHealth] = useState(null)
  const [bridge, setBridge] = useState({ available: false, detail: '未连接' })
  const [data, setData] = useState({})
  const [logs, setLogs] = useState([])
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const socketRef = useRef(null)

  const addLog = useCallback((level, message) => {
    setLogs((current) => [
      { id: `${Date.now()}-${Math.random()}`, level, message, time: new Date() },
      ...current,
    ].slice(0, 120))
  }, [])

  const applyBootstrap = useCallback((payload) => {
    if (payload.environment) setEnvironment(payload.environment)
    if (payload.features) setFeatures(payload.features)
    if (payload.maps) setMaps(payload.maps)
    if (payload.active_map) setActiveMap(payload.active_map)
    if (payload.runtime) setRuntime(payload.runtime)
    if (payload.health) setHealth(payload.health)
    if (payload.bridge) setBridge(payload.bridge)
    if (payload.latest) setData((current) => ({ ...current, ...payload.latest }))
  }, [])

  const handleEvent = useCallback((event) => {
    if (event.type === 'bootstrap') {
      applyBootstrap(event)
      return
    }
    if (event.type === 'status' && event.runtime) setRuntime(event.runtime)
    else if (event.type === 'health') setHealth(event)
    else if (event.type === 'bridge') setBridge(event)
    else if (event.type === 'log') addLog(event.level || 'INFO', event.message || '')
    else if (['map', 'local_costmap', 'global_costmap', 'pose', 'path', 'scan', 'telemetry', 'navigation', 'localization', 'route', 'arbiter', 'control'].includes(event.type)) {
      setData((current) => ({ ...current, [event.type]: event }))
    }
  }, [addLog, applyBootstrap])

  useEffect(() => {
    let closed = false
    let reconnectTimer
    let heartbeatTimer

    api('/api/bootstrap').then(applyBootstrap).catch((reason) => setError(reason.message))

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws`)
      socketRef.current = socket
      socket.onopen = () => {
        setConnected(true)
        setError('')
        heartbeatTimer = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'heartbeat' }))
        }, 8000)
      }
      socket.onmessage = (message) => {
        try { handleEvent(JSON.parse(message.data)) } catch { /* ignore non-protocol messages */ }
      }
      socket.onerror = () => setError('实时连接异常')
      socket.onclose = () => {
        setConnected(false)
        window.clearInterval(heartbeatTimer)
        if (!closed) reconnectTimer = window.setTimeout(connect, 1800)
      }
    }
    connect()
    return () => {
      closed = true
      window.clearTimeout(reconnectTimer)
      window.clearInterval(heartbeatTimer)
      socketRef.current?.close()
    }
  }, [applyBootstrap, handleEvent])

  const perform = useCallback(async (operation) => {
    setBusy(true)
    setError('')
    try {
      return await operation()
    } catch (reason) {
      setError(reason.message)
      addLog('ERROR', reason.message)
      throw reason
    } finally {
      setBusy(false)
    }
  }, [addLog])

  const setFeature = useCallback((feature) => perform(async () => {
    const result = await post('/api/system/feature', { feature })
    setRuntime(result.runtime)
  }), [perform])

  const stop = useCallback(() => perform(async () => {
    const result = await post('/api/system/stop')
    setRuntime(result.runtime)
  }), [perform])

  const command = useCallback((payload) => post('/api/command', payload).catch((reason) => {
    setError(reason.message)
    throw reason
  }), [])

  const applyMaps = useCallback((result) => {
    if (result.maps) setMaps(result.maps)
    if (result.active_map) setActiveMap(result.active_map)
    return result
  }, [])

  const saveMap = useCallback((name, profile) => perform(async () => {
    const result = applyMaps(await post('/api/maps/save', { name, ...profile }))
    addLog('INFO', `地图已保存：${name}`)
    return result
  }), [addLog, applyMaps, perform])

  const updateMap = useCallback((name, profile) => perform(async () => {
    const result = applyMaps(await post(`/api/maps/${encodeURIComponent(name)}`, profile))
    addLog('INFO', `地图档案已更新：${name}`)
    return result
  }), [addLog, applyMaps, perform])

  const activateMap = useCallback((name) => perform(async () => {
    const result = applyMaps(await post(`/api/maps/${encodeURIComponent(name)}/activate`))
    addLog('INFO', `已选择地图：${name}`)
    return result
  }), [addLog, applyMaps, perform])

  return {
    features,
    maps,
    activeMap,
    environment,
    runtime,
    health,
    bridge,
    data,
    logs,
    connected,
    busy,
    error,
    setError,
    setFeature,
    stop,
    command,
    saveMap,
    updateMap,
    activateMap,
  }
}
