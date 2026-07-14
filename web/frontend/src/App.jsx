import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, BatteryCharging, Gauge, Radio, Route, X } from 'lucide-react'
import { useConsole } from './hooks/useConsole'
import { Header, Sidebar } from './components/Chrome'
import FeatureControls from './components/Controls'
import MapCanvas from './components/MapCanvas'
import StatusPanel from './components/StatusPanel'

const CONFIRMED_FEATURES = new Set(['SLAM', 'NAV_DWA', 'NAV_TEB', 'TASK_ROUTE'])

function TelemetryBar({ data, health }) {
  const telemetry = data.telemetry || {}
  return (
    <div className="telemetry-bar">
      <span><BatteryCharging size={15} /><i>电压</i><strong>{telemetry.voltage == null ? '--' : `${telemetry.voltage.toFixed(2)} V`}</strong></span>
      <span><Gauge size={15} /><i>线速度</i><strong>{telemetry.linear == null ? '--' : `${telemetry.linear.toFixed(2)} m/s`}</strong></span>
      <span><Route size={15} /><i>角速度</i><strong>{telemetry.angular == null ? '--' : `${telemetry.angular.toFixed(2)} rad/s`}</strong></span>
      <span><Radio size={15} /><i>ROS 节点</i><strong>{health?.nodes?.length ?? 0}</strong></span>
    </div>
  )
}

export default function App() {
  const consoleState = useConsole()
  const transitioning = ['STARTING', 'STOPPING'].includes(consoleState.runtime.phase)
  const [selectedFeatureId, setSelectedFeatureId] = useState('IDLE')
  const [interaction, setInteraction] = useState('pan')
  const [selectedPoint, setSelectedPoint] = useState(null)
  const [selectedMapName, setSelectedMapName] = useState('')
  const [mappingWaypoints, setMappingWaypoints] = useState([])
  const [mappingDefaultId, setMappingDefaultId] = useState(null)
  const [routePreviewIds, setRoutePreviewIds] = useState([])
  const [pendingInitialization, setPendingInitialization] = useState(null)

  useEffect(() => {
    if (selectedFeatureId === 'IDLE' && consoleState.runtime.feature !== 'IDLE') {
      setSelectedFeatureId(consoleState.runtime.feature)
    }
  }, [consoleState.runtime.feature, selectedFeatureId])

  useEffect(() => {
    if (!selectedMapName && consoleState.maps.length) {
      setSelectedMapName(consoleState.activeMap || consoleState.maps[0].map_name)
    }
  }, [consoleState.activeMap, consoleState.maps, selectedMapName])

  useEffect(() => {
    setSelectedPoint(null)
  }, [consoleState.runtime.generation, selectedFeatureId])

  useEffect(() => {
    if (['finished', 'canceled', 'rejected', 'error', 'unavailable'].includes(consoleState.data.navigation?.state)) {
      setSelectedPoint(null)
    }
  }, [consoleState.data.navigation?.state])

  useEffect(() => {
    if (!pendingInitialization) return
    if (consoleState.runtime.feature !== pendingInitialization.feature) return
    if (!['READY', 'DEGRADED'].includes(consoleState.runtime.phase)) return
    const request = pendingInitialization
    setPendingInitialization(null)
    if (request.mapName && request.hasDefaultPose) {
      consoleState.command({ type: 'map_initial_pose', map_name: request.mapName }).catch(() => {})
    }
  }, [consoleState.command, consoleState.runtime.feature, consoleState.runtime.phase, pendingInitialization])

  const activeFeature = useMemo(
    () => consoleState.features.find((feature) => feature.id === selectedFeatureId)
      || consoleState.features.find((feature) => feature.id === consoleState.runtime.feature)
      || consoleState.features.find((feature) => feature.id === 'IDLE'),
    [consoleState.features, consoleState.runtime.feature, selectedFeatureId],
  )
  const selectedProfile = useMemo(
    () => consoleState.maps.find((profile) => profile.map_name === selectedMapName),
    [consoleState.maps, selectedMapName],
  )
  const markers = activeFeature?.surface === 'mapping' ? mappingWaypoints : selectedProfile?.waypoints || []
  const defaultMarkerId = activeFeature?.surface === 'mapping' ? mappingDefaultId : selectedProfile?.default_pose_id
  const routePreview = useMemo(() => {
    const points = selectedProfile?.waypoints || []
    return routePreviewIds.map((id) => points.find((point) => point.id === id)).filter(Boolean)
  }, [routePreviewIds, selectedProfile])

  const selectFeature = (featureId) => {
    setSelectedFeatureId(featureId)
    setRoutePreviewIds([])
    if (featureId === 'SLAM') setInteraction('waypoint')
    else if (['NAV_DWA', 'NAV_TEB'].includes(featureId)) setInteraction('goal')
    else setInteraction('pan')
    if (!CONFIRMED_FEATURES.has(featureId)) {
      consoleState.setFeature(featureId).catch(() => {})
    }
  }

  const startFeature = async (featureId, mapName = '') => {
    const profile = consoleState.maps.find((item) => item.map_name === mapName)
    if (mapName && mapName !== consoleState.activeMap) await consoleState.activateMap(mapName)
    setPendingInitialization({
      feature: featureId,
      mapName,
      hasDefaultPose: Boolean(profile?.default_pose_id),
    })
    await consoleState.setFeature(featureId)
  }

  const mapPoint = (point) => {
    setSelectedPoint(point)
  }

  const changeInteraction = (nextInteraction) => {
    setInteraction(nextInteraction)
    setSelectedPoint(null)
  }

  const addWaypoint = (name, point) => {
    const id = `point_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`
    setMappingWaypoints((current) => [...current, { id, name, ...point }])
    setMappingDefaultId((current) => current || id)
  }
  const removeWaypoint = (id) => {
    setMappingWaypoints((current) => current.filter((point) => point.id !== id))
    setMappingDefaultId((current) => current === id ? null : current)
  }

  const emergency = () => {
    consoleState.command({ type: 'emergency_stop' }).catch(() => {})
  }

  const onRoutePreview = useCallback((ids) => setRoutePreviewIds(ids), [])

  return (
    <div className="app-shell">
      <Header
        runtime={consoleState.runtime}
        health={consoleState.health}
        bridge={consoleState.bridge}
        telemetry={consoleState.data.telemetry}
        environment={consoleState.environment}
        connected={consoleState.connected}
        busy={consoleState.busy}
        onStop={() => consoleState.stop().catch(() => {})}
        onEmergency={emergency}
      />
      <Sidebar
        features={consoleState.features}
        selectedFeature={selectedFeatureId}
        runningFeature={consoleState.runtime.feature}
        busy={consoleState.busy || transitioning}
        onSelect={selectFeature}
      />
      <main className="workspace">
        <div className="workspace-titlebar">
          <div><span>{activeFeature?.group || 'system'}</span><strong>{activeFeature?.label || '系统待机'}</strong></div>
          <div className="runtime-message"><i className={`phase-dot phase-${consoleState.runtime.phase.toLowerCase()}`} />{consoleState.runtime.message || consoleState.bridge.detail}</div>
        </div>
        <div className="workspace-grid">
          <div className="visual-column">
            <MapCanvas
              data={consoleState.data}
              activeFeatureId={consoleState.runtime.feature}
              interaction={interaction}
              onMapPoint={mapPoint}
              selectedPoint={selectedPoint}
              markers={markers}
              defaultMarkerId={defaultMarkerId}
              routePreview={routePreview}
            />
            <TelemetryBar data={consoleState.data} health={consoleState.health} />
          </div>
          <aside className="inspector-column">
            <FeatureControls
              activeFeature={activeFeature}
              activeFeatureId={consoleState.runtime.feature}
              runtime={consoleState.runtime}
              command={consoleState.command}
              saveMap={consoleState.saveMap}
              updateMap={consoleState.updateMap}
              busy={consoleState.busy || transitioning}
              data={consoleState.data}
              interaction={interaction}
              setInteraction={changeInteraction}
              selectedPoint={selectedPoint}
              onSelectedPointChange={setSelectedPoint}
              maps={consoleState.maps}
              activeMap={consoleState.activeMap}
              selectedMapName={selectedMapName}
              onMapSelection={(name) => { setSelectedMapName(name); setRoutePreviewIds([]) }}
              onStartFeature={startFeature}
              mappingWaypoints={mappingWaypoints}
              mappingDefaultId={mappingDefaultId}
              onAddWaypoint={addWaypoint}
              onRemoveWaypoint={removeWaypoint}
              onSetDefault={setMappingDefaultId}
              onRoutePreview={onRoutePreview}
            />
            <StatusPanel activeFeature={activeFeature} health={consoleState.health} bridge={consoleState.bridge} connected={consoleState.connected} environment={consoleState.environment} telemetry={consoleState.data.telemetry} logs={consoleState.logs} />
          </aside>
        </div>
      </main>
      {consoleState.busy && <div className="busy-bar" />}
      {consoleState.error && (
        <div className="error-toast" role="alert">
          <AlertTriangle size={18} /><span>{consoleState.error}</span>
          <button type="button" onClick={() => consoleState.setError('')} aria-label="关闭"><X size={17} /></button>
        </div>
      )}
    </div>
  )
}
