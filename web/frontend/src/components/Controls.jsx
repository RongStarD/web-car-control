import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Ban,
  Check,
  ChevronDown,
  ChevronUp,
  Crosshair,
  ListOrdered,
  MapPin,
  Navigation,
  Play,
  Plus,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Square,
  Trash2,
} from 'lucide-react'

function Segmented({ value, onChange, items, label }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {items.map(({ id, label: itemLabel, icon: Icon }) => (
        <button type="button" className={value === id ? 'is-active' : ''} onClick={() => onChange(id)} key={id}>
          {Icon && <Icon size={15} />}{itemLabel}
        </button>
      ))}
    </div>
  )
}

function ManualPad({ command, compact = false }) {
  const [linear, setLinear] = useState(0.18)
  const [angular, setAngular] = useState(0.65)
  const timerRef = useRef(null)
  const activeRef = useRef(null)

  const send = useCallback((direction) => {
    const payload = {
      forward: { linear, angular: 0 },
      back: { linear: -linear, angular: 0 },
      left: { linear: 0, angular },
      right: { linear: 0, angular: -angular },
    }[direction] || { linear: 0, angular: 0 }
    command({ type: 'drive', ...payload }).catch(() => {})
  }, [angular, command, linear])

  const stop = useCallback(() => {
    window.clearInterval(timerRef.current)
    timerRef.current = null
    activeRef.current = null
    send('stop')
  }, [send])

  const start = useCallback((direction) => {
    stop()
    activeRef.current = direction
    send(direction)
    timerRef.current = window.setInterval(() => send(direction), 110)
  }, [send, stop])

  useEffect(() => {
    const keys = { KeyW: 'forward', ArrowUp: 'forward', KeyS: 'back', ArrowDown: 'back', KeyA: 'left', ArrowLeft: 'left', KeyD: 'right', ArrowRight: 'right' }
    const keyDown = (event) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return
      const direction = keys[event.code]
      if (direction && activeRef.current !== direction) { event.preventDefault(); start(direction) }
      if (event.code === 'Space') { event.preventDefault(); stop() }
    }
    const keyUp = (event) => { if (keys[event.code] === activeRef.current) stop() }
    window.addEventListener('keydown', keyDown)
    window.addEventListener('keyup', keyUp)
    window.addEventListener('blur', stop)
    return () => {
      window.removeEventListener('keydown', keyDown)
      window.removeEventListener('keyup', keyUp)
      window.removeEventListener('blur', stop)
      window.clearInterval(timerRef.current)
    }
  }, [start, stop])

  const pressProps = (direction) => ({
    onPointerDown: (event) => { event.currentTarget.setPointerCapture(event.pointerId); start(direction) },
    onPointerUp: stop,
    onPointerCancel: stop,
    onContextMenu: (event) => event.preventDefault(),
  })

  return (
    <div className={compact ? 'manual-control compact' : 'manual-control'}>
      <div className="direction-pad" aria-label="方向控制">
        <button type="button" className="up" {...pressProps('forward')} aria-label="前进" title="前进"><ArrowUp /></button>
        <button type="button" className="left" {...pressProps('left')} aria-label="左转" title="左转"><ArrowLeft /></button>
        <button type="button" className="stop" onClick={stop} aria-label="停车" title="停车"><Square size={16} fill="currentColor" /></button>
        <button type="button" className="right" {...pressProps('right')} aria-label="右转" title="右转"><ArrowRight /></button>
        <button type="button" className="down" {...pressProps('back')} aria-label="后退" title="后退"><ArrowDown /></button>
      </div>
      <div className="drive-sliders">
        <label><span>线速度</span><output>{linear.toFixed(2)} m/s</output><input type="range" min="0.05" max="0.30" step="0.01" value={linear} onChange={(event) => setLinear(Number(event.target.value))} /></label>
        <label><span>角速度</span><output>{angular.toFixed(2)} rad/s</output><input type="range" min="0.20" max="1.00" step="0.05" value={angular} onChange={(event) => setAngular(Number(event.target.value))} /></label>
      </div>
    </div>
  )
}

function CoordinateReadout({ point }) {
  return (
    <div className="coordinate-readout">
      <span>X <strong>{point ? point.x.toFixed(2) : '--'}</strong></span>
      <span>Y <strong>{point ? point.y.toFixed(2) : '--'}</strong></span>
      <span>θ <strong>{point ? point.yaw.toFixed(2) : '--'}</strong></span>
    </div>
  )
}

function PoseAngleControl({ point, onChange }) {
  const degrees = point ? Math.round(point.yaw * 180 / Math.PI) : 0
  return (
    <label className="pose-angle-control">
      <span>初始朝向</span>
      <output>{degrees}°</output>
      <input
        type="range"
        min="-180"
        max="180"
        step="1"
        value={degrees}
        disabled={!point}
        onChange={(event) => onChange({ ...point, yaw: Number(event.target.value) * Math.PI / 180 })}
      />
    </label>
  )
}

function StartFeature({ label, onStart, busy, disabled = false }) {
  return (
    <div className="feature-confirm">
      <Play size={18} />
      <div><strong>{label}</strong><span>确认后才会启动所需 ROS 节点</span></div>
      <button type="button" className="command-button primary" onClick={onStart} disabled={busy || disabled}><Play size={16} />启动</button>
    </div>
  )
}

function LocalizationStatus({ data }) {
  const state = data.localization?.state || (data.pose?.localized ? 'ready' : 'waiting_initial_pose')
  const labels = {
    waiting_initial_pose: '等待初始位姿',
    adjusting: 'AMCL 调整中',
    ready: 'AMCL 定位就绪',
    timeout: 'AMCL 定位超时',
  }
  return (
    <div className="nav-state-line localization-state">
      <i className={`nav-state ${state}`} />
      <span>{labels[state] || state}</span>
      {state === 'adjusting' && <strong>扫描匹配</strong>}
    </div>
  )
}

function MapSelector({ maps, value, onChange, disabled = false }) {
  return (
    <label className="field-row">
      <span>地图</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        {maps.map((map) => <option value={map.map_name} key={map.map_name}>{map.label || map.map_name}</option>)}
      </select>
    </label>
  )
}

function ManualControls({ command }) {
  return <ManualPad command={command} />
}

function MappingControls(props) {
  const [name, setName] = useState(`map_${new Date().toISOString().slice(0, 10).replaceAll('-', '')}`)
  const [pointName, setPointName] = useState('')
  const active = props.activeFeatureId === 'SLAM'

  if (!active) {
    return <StartFeature label="启动地图构建" onStart={() => props.onStartFeature('SLAM')} busy={props.busy} />
  }

  const addPoint = () => {
    if (!props.selectedPoint || !pointName.trim()) return
    props.onAddWaypoint(pointName.trim(), props.selectedPoint)
    setPointName('')
  }

  return (
    <>
      <ManualPad command={props.command} compact />
      <div className="control-section">
        <div className="section-title"><MapPin size={16} />标记点位</div>
        <Segmented
          label="建图操作"
          value={props.interaction}
          onChange={props.setInteraction}
          items={[
            { id: 'waypoint', label: '标记点位', icon: MapPin },
            { id: 'pan', label: '平移地图', icon: SlidersHorizontal },
          ]}
        />
        <CoordinateReadout point={props.selectedPoint} />
        <div className="point-add-row">
          <input value={pointName} onChange={(event) => setPointName(event.target.value)} maxLength={64} placeholder="点位名称" />
          <button type="button" className="icon-command" onClick={addPoint} disabled={!props.selectedPoint || !pointName.trim()} title="添加点位" aria-label="添加点位"><Plus size={17} /></button>
        </div>
        <div className="waypoint-list compact-list">
          {props.mappingWaypoints.map((point, index) => (
            <div className={point.id === props.mappingDefaultId ? 'waypoint-row is-default' : 'waypoint-row'} key={point.id}>
              <b>{index + 1}</b><div><strong>{point.name}</strong><span>{point.x.toFixed(2)}, {point.y.toFixed(2)} · {point.yaw.toFixed(2)} rad</span></div>
              <button type="button" className="icon-command" onClick={() => props.onSetDefault(point.id)} title="设为默认位姿" aria-label="设为默认位姿"><Check size={15} /></button>
              <button type="button" className="icon-command danger-text" onClick={() => props.onRemoveWaypoint(point.id)} title="删除点位" aria-label="删除点位"><Trash2 size={15} /></button>
            </div>
          ))}
          {!props.mappingWaypoints.length && <div className="empty-compact">尚未记录点位</div>}
        </div>
      </div>
      <div className="control-section">
        <div className="section-title"><Save size={16} />地图与点位</div>
        <form className="save-row" onSubmit={(event) => {
          event.preventDefault()
          props.saveMap(name, {
            label: name,
            waypoints: props.mappingWaypoints,
            default_pose_id: props.mappingDefaultId,
            routes: [],
          }).catch(() => {})
        }}>
          <input value={name} onChange={(event) => setName(event.target.value.replace(/[^A-Za-z0-9_-]/g, ''))} aria-label="地图名称" />
          <button type="submit" className="command-button primary" disabled={props.busy || !name || !props.mappingDefaultId}><Save size={16} />一并保存</button>
        </form>
      </div>
    </>
  )
}

function NavigationControls(props) {
  const active = props.activeFeatureId === props.activeFeature.id
  return (
    <>
      <div className="control-section">
        <div className="section-title"><Navigation size={16} />导航地图</div>
        <MapSelector maps={props.maps} value={props.selectedMapName} onChange={props.onMapSelection} disabled={active} />
        <div className="inline-warning">常规导航不会应用地图默认位姿，启动后请手动设置初始位姿。</div>
      </div>
      {!active && <StartFeature label={`启动${props.activeFeature.label}`} onStart={() => props.onStartFeature(props.activeFeature.id, props.selectedMapName)} busy={props.busy} disabled={!props.selectedMapName} />}
      {active && (
        <>
          <div className="control-section">
            <div className="section-title"><Crosshair size={16} />地图操作</div>
            <Segmented
              label="地图操作"
              value={props.interaction}
              onChange={props.setInteraction}
              items={[
                { id: 'goal', label: '目标点', icon: MapPin },
                { id: 'initial_pose', label: '初始位姿', icon: Navigation },
                { id: 'pan', label: '平移', icon: SlidersHorizontal },
              ]}
            />
            <CoordinateReadout point={props.selectedPoint} />
            {props.interaction === 'initial_pose' && (
              <PoseAngleControl point={props.selectedPoint} onChange={props.onSelectedPointChange} />
            )}
            <LocalizationStatus data={props.data} />
            {props.interaction !== 'pan' && (
              <button
                type="button"
                className="command-button wide primary selection-command"
                disabled={!props.selectedPoint || (props.interaction === 'goal' && props.data.pose?.localized !== true)}
                onClick={() => props.command({ type: props.interaction, ...props.selectedPoint }).catch(() => {})}
              >
                {props.interaction === 'initial_pose' ? <Navigation size={16} /> : <MapPin size={16} />}
                {props.interaction === 'initial_pose' ? '应用初始位姿' : '发送目标点'}
              </button>
            )}
          </div>
          <div className="control-section">
            <div className="section-title"><Navigation size={16} />导航状态</div>
            <div className="nav-state-line"><i className={`nav-state ${props.data.navigation?.state || 'idle'}`} /><span>{props.data.navigation?.state || 'idle'}</span>{props.data.navigation?.distance_remaining != null && <strong>{props.data.navigation.distance_remaining.toFixed(2)} m</strong>}</div>
            <button type="button" className="command-button wide" onClick={() => props.command({ type: 'cancel_goal' }).catch(() => {})}><Ban size={17} />取消目标</button>
          </div>
        </>
      )}
    </>
  )
}

function TaskControls(props) {
  const active = props.activeFeatureId === 'TASK_ROUTE'
  const profile = props.maps.find((item) => item.map_name === props.selectedMapName)
  const [routeId, setRouteId] = useState('')
  const [routeName, setRouteName] = useState('')
  const [pointIds, setPointIds] = useState([])
  const [dirty, setDirty] = useState(false)
  const routes = profile?.routes || []
  const points = profile?.waypoints || []
  const selectedRoute = routes.find((item) => item.id === routeId)

  useEffect(() => {
    const route = routes.find((item) => item.id === routeId) || routes[0]
    if (route) {
      setRouteId(route.id)
      setRouteName(route.name)
      setPointIds(route.waypoint_ids)
    } else {
      setRouteId('')
      setRouteName('')
      setPointIds([])
    }
    setDirty(false)
  }, [profile?.map_name]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    props.onRoutePreview(pointIds)
    return () => props.onRoutePreview([])
  }, [pointIds, props.onRoutePreview])

  const selectRoute = (id) => {
    if (!id) {
      setRouteId('')
      setRouteName('')
      setPointIds([])
      setDirty(true)
      return
    }
    const route = routes.find((item) => item.id === id)
    setRouteId(route.id)
    setRouteName(route.name)
    setPointIds(route.waypoint_ids)
    setDirty(false)
  }
  const changePoints = (next) => { setPointIds(next); setDirty(true) }
  const move = (index, offset) => {
    const next = [...pointIds]
    const target = index + offset
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    changePoints(next)
  }
  const saveRoute = async () => {
    if (!profile || !routeName.trim() || !pointIds.length) return
    const id = routeId || `route_${Date.now().toString(36)}`
    const route = { id, name: routeName.trim(), waypoint_ids: pointIds }
    const nextRoutes = routeId ? routes.map((item) => item.id === routeId ? route : item) : [...routes, route]
    await props.updateMap(profile.map_name, { ...profile, routes: nextRoutes })
    setRouteId(id)
    setDirty(false)
  }

  return (
    <>
      <div className="control-section">
        <div className="section-title"><ListOrdered size={16} />任务地图</div>
        <MapSelector maps={props.maps} value={props.selectedMapName} onChange={props.onMapSelection} disabled={active} />
        {!profile?.default_pose_id && <div className="inline-warning">任务地图必须先在建图模块记录默认位姿。</div>}
      </div>
      {!active && <StartFeature label="启动路线任务" onStart={() => props.onStartFeature('TASK_ROUTE', props.selectedMapName)} busy={props.busy} disabled={!profile?.default_pose_id} />}
      <div className="control-section">
        <div className="section-title"><ListOrdered size={16} />路线编排</div>
        <label className="field-row"><span>路线</span><select value={routeId} onChange={(event) => selectRoute(event.target.value)}><option value="">新建路线</option>{routes.map((route) => <option value={route.id} key={route.id}>{route.name}</option>)}</select></label>
        <input className="full-input" value={routeName} onChange={(event) => { setRouteName(event.target.value); setDirty(true) }} maxLength={64} placeholder="路线名称，例如：厨房到 808" />
        <div className="available-points">
          {points.map((point) => <button type="button" onClick={() => changePoints([...pointIds, point.id])} key={point.id}><Plus size={13} />{point.name}</button>)}
        </div>
        <div className="route-list compact-list">
          {pointIds.map((id, index) => {
            const point = points.find((item) => item.id === id)
            return (
              <div className="route-row" key={`${id}-${index}`}>
                <b>{index + 1}</b><strong>{point?.name || id}</strong>
                <button type="button" className="icon-command" onClick={() => move(index, -1)} disabled={index === 0} title="上移" aria-label="上移"><ChevronUp size={15} /></button>
                <button type="button" className="icon-command" onClick={() => move(index, 1)} disabled={index === pointIds.length - 1} title="下移" aria-label="下移"><ChevronDown size={15} /></button>
                <button type="button" className="icon-command danger-text" onClick={() => changePoints(pointIds.filter((_, itemIndex) => itemIndex !== index))} title="移除" aria-label="移除"><Trash2 size={15} /></button>
              </div>
            )
          })}
          {!pointIds.length && <div className="empty-compact">从上方点位中依次添加</div>}
        </div>
        <button type="button" className="command-button wide" onClick={() => saveRoute().catch(() => {})} disabled={!routeName.trim() || !pointIds.length || !dirty}><Save size={16} />保存路线</button>
      </div>
      {active && (
        <div className="control-section">
          <div className="section-title"><Play size={16} />任务执行</div>
          <LocalizationStatus data={props.data} />
          <div className="nav-state-line"><i className={`nav-state ${props.data.route?.state || 'idle'}`} /><span>{props.data.route?.state || 'idle'}</span>{props.data.route?.total > 0 && <strong>{Math.max(0, props.data.route.index + 1)} / {props.data.route.total}</strong>}</div>
          <div className="button-pair">
            <button type="button" className="command-button primary" disabled={!selectedRoute || dirty || props.data.pose?.localized !== true || ['starting', 'running'].includes(props.data.route?.state)} onClick={() => props.command({ type: 'route_start', map_name: profile.map_name, route_id: selectedRoute.id }).catch(() => {})}><Play size={16} />启动路线</button>
            <button type="button" className="command-button" onClick={() => props.command({ type: 'cancel_goal' }).catch(() => {})}><Ban size={16} />取消</button>
          </div>
        </div>
      )}
    </>
  )
}

export default function FeatureControls(props) {
  const surface = props.activeFeature?.surface || 'overview'
  return (
    <section className="control-panel">
      <div className="panel-heading">
        <div><span>功能控制</span><strong>{props.activeFeature?.label || '待机'}</strong></div>
        <RotateCcw size={17} />
      </div>
      <div className="control-panel-body">
        {surface === 'manual' && <ManualControls {...props} />}
        {surface === 'mapping' && <MappingControls {...props} />}
        {surface === 'navigation' && <NavigationControls {...props} />}
        {surface === 'task' && <TaskControls {...props} />}
        {surface === 'overview' && <div className="idle-panel"><Square size={24} /><strong>系统待机</strong></div>}
      </div>
    </section>
  )
}
