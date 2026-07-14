import {
  Activity,
  CircleStop,
  OctagonAlert,
  Radio,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { featureMeta, groupLabels } from '../features/registry'

export function Header({ runtime, health, bridge, telemetry, environment, connected, busy, onStop, onEmergency }) {
  const transitioning = ['STARTING', 'STOPPING'].includes(runtime.phase)
  const phaseClass = runtime.phase === 'ERROR' ? 'error' : runtime.phase === 'DEGRADED' ? 'warn' : runtime.phase === 'READY' ? 'ok' : 'neutral'
  const basePresent = health?.hardware?.base_serial?.present === true
  const telemetryFresh = telemetry?.voltage != null && Date.now() / 1000 - telemetry.stamp < 3
  let vehicleLabel = '状态未知'
  let vehicleClass = 'neutral'
  if (environment === 'demo') {
    vehicleLabel = '模拟数据'
    vehicleClass = 'warn'
  } else if (!connected) {
    vehicleLabel = 'Jetson 断开'
    vehicleClass = 'error'
  } else if (!basePresent) {
    vehicleLabel = '底盘设备缺失'
    vehicleClass = 'error'
  } else if (runtime.feature === 'IDLE') {
    vehicleLabel = '底盘待机'
    vehicleClass = 'neutral'
  } else if (telemetryFresh) {
    vehicleLabel = '小车在线'
    vehicleClass = 'ok'
  } else if (bridge?.available) {
    vehicleLabel = '等待底盘响应'
    vehicleClass = 'warn'
  } else {
    vehicleLabel = 'ROS 未连接'
    vehicleClass = 'error'
  }
  return (
    <header className="app-header">
      <div className="brand-block">
        <div className="brand-mark"><Radio size={21} /></div>
        <div><strong>OHCar</strong><span>控制台</span></div>
      </div>
      <div className="header-status">
        <span className={`status-pill ${connected ? 'ok' : 'error'}`}>
          {connected ? <Wifi size={14} /> : <WifiOff size={14} />}
          {environment === 'demo' ? '本机演示' : connected ? 'Jetson 在线' : 'Jetson 断开'}
        </span>
        <span className={`status-pill ${vehicleClass}`}><Radio size={14} />{vehicleLabel}</span>
        <span className={`status-pill ${phaseClass}`}><Activity size={14} />{runtime.phase}</span>
      </div>
      <div className="header-actions">
        <button type="button" className="command-button danger" onClick={onEmergency}>
          <OctagonAlert size={18} />急停
        </button>
        <button type="button" className="command-button" onClick={onStop} disabled={busy || transitioning || runtime.phase === 'IDLE'}>
          <CircleStop size={18} />停止功能
        </button>
      </div>
    </header>
  )
}

export function Sidebar({ features, selectedFeature, runningFeature, busy, onSelect }) {
  const groups = features.reduce((result, feature) => {
    if (feature.id === 'IDLE' || feature.visible === false) return result
    result[feature.group] = [...(result[feature.group] || []), feature]
    return result
  }, {})
  return (
    <nav className="feature-nav" aria-label="功能区">
      {Object.entries(groups).map(([group, items]) => (
        <div className="feature-group" key={group}>
          <div className="feature-group-label">{groupLabels[group] || group}</div>
          <div className="feature-list">
            {items.map((feature) => {
              const { icon: Icon, accent } = featureMeta(feature)
              const selected = feature.id === selectedFeature
              const running = feature.id === runningFeature
              return (
                <button
                  type="button"
                  className={`feature-button accent-${accent}${selected ? ' is-active' : ''}${running ? ' is-running' : ''}`}
                  onClick={() => onSelect(feature.id)}
                  disabled={busy || !feature.enabled}
                  title={feature.enabled ? feature.label : feature.blocked_reason}
                  key={feature.id}
                >
                  <Icon size={19} />
                  <span>{feature.label}</span>
                  <i className="feature-state-dot" />
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </nav>
  )
}
