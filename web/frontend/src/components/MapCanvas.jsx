import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Crosshair, LocateFixed, Minus, Plus } from 'lucide-react'
import { layerRegistry } from '../features/registry'

function decodeRle(event) {
  if (!event?.data_rle) return null
  const values = new Int16Array(event.width * event.height)
  let offset = 0
  for (const [value, count] of event.data_rle) {
    values.fill(value, offset, offset + count)
    offset += count
  }
  return values
}

function createGridImage(event, values, costmap = false) {
  if (!event || !values) return null
  const canvas = document.createElement('canvas')
  canvas.width = event.width
  canvas.height = event.height
  const context = canvas.getContext('2d')
  const image = context.createImageData(event.width, event.height)
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index]
    const pixel = index * 4
    if (costmap) {
      if (value <= 0) {
        image.data[pixel + 3] = 0
      } else {
        image.data[pixel] = value > 70 ? 210 : 238
        image.data[pixel + 1] = value > 70 ? 58 : 151
        image.data[pixel + 2] = value > 70 ? 48 : 34
        image.data[pixel + 3] = Math.min(185, 45 + value)
      }
    } else {
      const color = value < 0 ? [205, 212, 211] : value > 60 ? [45, 62, 67] : [248, 249, 247]
      image.data[pixel] = color[0]
      image.data[pixel + 1] = color[1]
      image.data[pixel + 2] = color[2]
      image.data[pixel + 3] = 255
    }
  }
  context.putImageData(image, 0, 0)
  return canvas
}

function worldBounds(map) {
  if (!map) return { minX: -5, minY: -4, maxX: 5, maxY: 4 }
  return {
    minX: map.origin.x,
    minY: map.origin.y,
    maxX: map.origin.x + map.width * map.resolution,
    maxY: map.origin.y + map.height * map.resolution,
  }
}

function rotatedScanPoint(scanX, scanY, frame, anchor) {
  if (frame === 'map') {
    if (!anchor) return { x: scanX, y: scanY }
    return {
      x: 2 * anchor.x - scanX,
      y: 2 * anchor.y - scanY,
    }
  }

  const cos = Math.cos(anchor.yaw)
  const sin = Math.sin(anchor.yaw)
  return {
    x: anchor.x - scanX * cos + scanY * sin,
    y: anchor.y - scanX * sin - scanY * cos,
  }
}

export default function MapCanvas({ data, activeFeatureId, interaction, onMapPoint, selectedPoint = null, markers = [], defaultMarkerId = null, routePreview = [] }) {
  const canvasRef = useRef(null)
  const shellRef = useRef(null)
  const dragRef = useRef(null)
  const [size, setSize] = useState({ width: 800, height: 560 })
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [layers, setLayers] = useState(() => Object.fromEntries(layerRegistry.map((item) => [item.id, item.defaultOn])))
  const [cursor, setCursor] = useState(null)

  const mapValues = useMemo(() => decodeRle(data.map), [data.map])
  const localValues = useMemo(() => decodeRle(data.local_costmap), [data.local_costmap])
  const globalValues = useMemo(() => decodeRle(data.global_costmap), [data.global_costmap])
  const mapImage = useMemo(() => createGridImage(data.map, mapValues), [data.map, mapValues])
  const localImage = useMemo(() => createGridImage(data.local_costmap, localValues, true), [data.local_costmap, localValues])
  const globalImage = useMemo(() => createGridImage(data.global_costmap, globalValues, true), [data.global_costmap, globalValues])
  const bounds = useMemo(() => worldBounds(data.map), [data.map])

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(320, Math.round(entry.contentRect.width))
      const height = Math.max(340, Math.round(entry.contentRect.height))
      setSize({ width, height })
    })
    if (shellRef.current) observer.observe(shellRef.current)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    setCursor(null)
  }, [activeFeatureId, interaction])

  useEffect(() => {
    setCursor(selectedPoint)
  }, [selectedPoint])

  useEffect(() => {
    if (['finished', 'canceled', 'rejected', 'error', 'unavailable'].includes(data.navigation?.state)) {
      setCursor(null)
    }
  }, [data.navigation?.state])

  const view = useMemo(() => {
    const worldWidth = Math.max(0.01, bounds.maxX - bounds.minX)
    const worldHeight = Math.max(0.01, bounds.maxY - bounds.minY)
    const baseScale = Math.min((size.width - 48) / worldWidth, (size.height - 48) / worldHeight)
    const scale = baseScale * zoom
    const drawnWidth = worldWidth * scale
    const drawnHeight = worldHeight * scale
    const offsetX = (size.width - drawnWidth) / 2 + pan.x
    const offsetY = (size.height - drawnHeight) / 2 + pan.y
    return { scale, offsetX, offsetY }
  }, [bounds, pan, size, zoom])

  const toScreen = useCallback((x, y) => ({
    x: view.offsetX + (x - bounds.minX) * view.scale,
    y: size.height - view.offsetY - (y - bounds.minY) * view.scale,
  }), [bounds, size.height, view])

  const toWorld = useCallback((x, y) => ({
    x: bounds.minX + (x - view.offsetX) / view.scale,
    y: bounds.minY + (size.height - y - view.offsetY) / view.scale,
  }), [bounds, size.height, view])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ratio = window.devicePixelRatio || 1
    canvas.width = size.width * ratio
    canvas.height = size.height * ratio
    canvas.style.width = `${size.width}px`
    canvas.style.height = `${size.height}px`
    const context = canvas.getContext('2d')
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.clearRect(0, 0, size.width, size.height)
    context.fillStyle = '#e8edeb'
    context.fillRect(0, 0, size.width, size.height)

    context.strokeStyle = '#d5dcda'
    context.lineWidth = 1
    const meter = view.scale
    if (meter > 18) {
      const startX = Math.floor(bounds.minX)
      const endX = Math.ceil(bounds.maxX)
      const startY = Math.floor(bounds.minY)
      const endY = Math.ceil(bounds.maxY)
      for (let x = startX; x <= endX; x += 1) {
        const point = toScreen(x, bounds.minY)
        context.beginPath(); context.moveTo(point.x, 0); context.lineTo(point.x, size.height); context.stroke()
      }
      for (let y = startY; y <= endY; y += 1) {
        const point = toScreen(bounds.minX, y)
        context.beginPath(); context.moveTo(0, point.y); context.lineTo(size.width, point.y); context.stroke()
      }
    }

    const drawGrid = (event, image, enabled) => {
      if (!event || !image || !enabled || (event.frame && event.frame !== 'map')) return
      const origin = toScreen(event.origin.x, event.origin.y)
      const width = event.width * event.resolution * view.scale
      const height = event.height * event.resolution * view.scale
      context.save()
      context.imageSmoothingEnabled = false
      context.globalAlpha = event.type === 'map' ? 1 : 0.72
      context.translate(origin.x, origin.y)
      context.rotate(-event.origin.yaw)
      context.scale(1, -1)
      context.drawImage(image, 0, 0, width, height)
      context.restore()
    }

    drawGrid(data.map, mapImage, layers.map)
    drawGrid(data.global_costmap, globalImage, layers.global)
    drawGrid(data.local_costmap, localImage, layers.local)

    if (routePreview.length > 1) {
      context.save()
      context.strokeStyle = '#b46b12'
      context.lineWidth = 2
      context.setLineDash([7, 5])
      context.beginPath()
      routePreview.forEach((point, index) => {
        const screen = toScreen(point.x, point.y)
        if (index === 0) context.moveTo(screen.x, screen.y)
        else context.lineTo(screen.x, screen.y)
      })
      context.stroke()
      context.restore()
    }

    markers.forEach((marker, index) => {
      const point = toScreen(marker.x, marker.y)
      const isDefault = marker.id === defaultMarkerId
      context.save()
      context.translate(point.x, point.y)
      context.rotate(-marker.yaw)
      context.strokeStyle = isDefault ? '#167d75' : '#b46b12'
      context.fillStyle = isDefault ? '#dff1ec' : '#fff4df'
      context.lineWidth = 2
      context.beginPath(); context.arc(0, 0, 8, 0, Math.PI * 2); context.fill(); context.stroke()
      context.beginPath(); context.moveTo(7, 0); context.lineTo(18, 0); context.lineTo(14, -4); context.moveTo(18, 0); context.lineTo(14, 4); context.stroke()
      context.restore()
      context.fillStyle = '#26383d'
      context.font = '600 11px "Segoe UI", sans-serif'
      context.fillText(`${index + 1} ${marker.name}`, point.x + 11, point.y - 10)
    })

    const navigationActive = ['NAV_DWA', 'NAV_TEB', 'TASK_ROUTE'].includes(activeFeatureId)
      && ['accepted', 'running'].includes(data.navigation?.state)
    if (layers.path && navigationActive && data.path?.points?.length) {
      context.strokeStyle = '#2463a9'
      context.lineWidth = 3
      context.lineJoin = 'round'
      context.beginPath()
      data.path.points.forEach(([x, y], index) => {
        const point = toScreen(x, y)
        if (index === 0) context.moveTo(point.x, point.y)
        else context.lineTo(point.x, point.y)
      })
      context.stroke()
    }

    const pose = data.scan?.robot_pose || data.pose
    const scanAnchor = data.scan?.origin || pose
    const localizationReady = !['NAV_DWA', 'NAV_TEB', 'TASK_ROUTE'].includes(activeFeatureId)
      || data.scan?.localized !== false
    if (layers.scan && localizationReady && data.scan?.points?.length && (data.scan.frame === 'map' || scanAnchor)) {
      context.fillStyle = '#167d75'
      for (const [scanX, scanY] of data.scan.points) {
        const visualPoint = rotatedScanPoint(scanX, scanY, data.scan.frame, scanAnchor)
        const point = toScreen(visualPoint.x, visualPoint.y)
        context.fillRect(point.x - 1, point.y - 1, 2, 2)
      }
    }

    if (pose && (!['NAV_DWA', 'NAV_TEB', 'TASK_ROUTE'].includes(activeFeatureId) || pose.localized !== false)) {
      const robot = toScreen(pose.x, pose.y)
      context.save()
      context.translate(robot.x, robot.y)
      context.rotate(-pose.yaw)
      context.fillStyle = '#f3b33f'
      context.strokeStyle = '#28383d'
      context.lineWidth = 2
      context.beginPath()
      context.moveTo(13, 0)
      context.lineTo(-9, -9)
      context.lineTo(-6, 0)
      context.lineTo(-9, 9)
      context.closePath()
      context.fill(); context.stroke()
      context.restore()
    }

    if (cursor && interaction !== 'pan') {
      const point = toScreen(cursor.x, cursor.y)
      context.strokeStyle = interaction === 'goal' ? '#c43c3c' : '#167d75'
      context.lineWidth = 2
      context.beginPath(); context.arc(point.x, point.y, 8, 0, Math.PI * 2); context.stroke()
      context.beginPath(); context.moveTo(point.x - 13, point.y); context.lineTo(point.x + 13, point.y); context.stroke()
      context.beginPath(); context.moveTo(point.x, point.y - 13); context.lineTo(point.x, point.y + 13); context.stroke()
      context.save()
      context.translate(point.x, point.y)
      context.rotate(-cursor.yaw)
      context.beginPath(); context.moveTo(0, 0); context.lineTo(28, 0); context.lineTo(22, -5); context.moveTo(28, 0); context.lineTo(22, 5); context.stroke()
      context.restore()
    }
  }, [activeFeatureId, bounds, cursor, data, defaultMarkerId, globalImage, interaction, layers, localImage, mapImage, markers, routePreview, size, toScreen, view])

  const pointerDown = (event) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    const rect = event.currentTarget.getBoundingClientRect()
    const screen = { x: event.clientX - rect.left, y: event.clientY - rect.top }
    if (interaction === 'pan') {
      dragRef.current = { mode: 'pan', x: event.clientX, y: event.clientY, pan, moved: false }
      return
    }
    const start = toWorld(screen.x, screen.y)
    dragRef.current = { mode: 'select', screen, start }
    setCursor({ ...start, yaw: 0 })
  }
  const pointerMove = (event) => {
    if (!dragRef.current) return
    if (dragRef.current.mode === 'select') {
      const rect = event.currentTarget.getBoundingClientRect()
      const endScreen = { x: event.clientX - rect.left, y: event.clientY - rect.top }
      const end = toWorld(endScreen.x, endScreen.y)
      const distance = Math.hypot(endScreen.x - dragRef.current.screen.x, endScreen.y - dragRef.current.screen.y)
      const yaw = distance > 6 ? Math.atan2(end.y - dragRef.current.start.y, end.x - dragRef.current.start.x) : 0
      setCursor({ ...dragRef.current.start, yaw })
      return
    }
    const dx = event.clientX - dragRef.current.x
    const dy = event.clientY - dragRef.current.y
    if (Math.abs(dx) + Math.abs(dy) > 4) dragRef.current.moved = true
    if (dragRef.current.moved) setPan({ x: dragRef.current.pan.x + dx, y: dragRef.current.pan.y - dy })
  }
  const pointerUp = (event) => {
    const drag = dragRef.current
    dragRef.current = null
    if (drag?.mode === 'select') {
      const rect = event.currentTarget.getBoundingClientRect()
      const endScreen = { x: event.clientX - rect.left, y: event.clientY - rect.top }
      const end = toWorld(endScreen.x, endScreen.y)
      const distance = Math.hypot(endScreen.x - drag.screen.x, endScreen.y - drag.screen.y)
      const point = {
        ...drag.start,
        yaw: distance > 6 ? Math.atan2(end.y - drag.start.y, end.x - drag.start.x) : 0,
      }
      setCursor(point)
      onMapPoint?.(point)
    }
  }
  const wheel = (event) => {
    event.preventDefault()
    setZoom((value) => Math.max(0.6, Math.min(5, value * (event.deltaY > 0 ? 0.9 : 1.1))))
  }
  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }) }

  return (
    <section className="map-shell" ref={shellRef} aria-label="地图工作区">
      <canvas
        ref={canvasRef}
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={pointerUp}
        onPointerCancel={() => { dragRef.current = null }}
        onWheel={wheel}
      />
      <div className="map-layer-bar" role="toolbar" aria-label="地图图层">
        {layerRegistry.map(({ id, label, icon: Icon }) => (
          <button
            type="button"
            className={layers[id] ? 'icon-button is-active' : 'icon-button'}
            onClick={() => setLayers((current) => ({ ...current, [id]: !current[id] }))}
            title={label}
            aria-label={label}
            aria-pressed={layers[id]}
            key={id}
          ><Icon size={18} /></button>
        ))}
      </div>
      <div className="map-zoom-bar" role="toolbar" aria-label="地图缩放">
        <button type="button" className="icon-button" onClick={() => setZoom((value) => Math.min(5, value * 1.2))} title="放大" aria-label="放大"><Plus size={18} /></button>
        <button type="button" className="icon-button" onClick={resetView} title="适配地图" aria-label="适配地图"><LocateFixed size={18} /></button>
        <button type="button" className="icon-button" onClick={() => setZoom((value) => Math.max(0.6, value / 1.2))} title="缩小" aria-label="缩小"><Minus size={18} /></button>
      </div>
      {!data.map && (
        <div className="map-empty">
          <Crosshair size={28} />
          <span>{activeFeatureId === 'SLAM' ? '等待建图数据' : ['NAV_DWA', 'NAV_TEB'].includes(activeFeatureId) ? '等待地图服务' : '当前功能不发布地图'}</span>
        </div>
      )}
      <div className="map-scale">{Math.round(zoom * 100)}%</div>
    </section>
  )
}
