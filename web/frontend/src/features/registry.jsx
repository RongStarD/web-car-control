import {
  Bot,
  ClipboardList,
  Crosshair,
  Eye,
  Gauge,
  Map as MapIcon,
  Navigation,
  Radar,
  Route,
  ShieldAlert,
} from 'lucide-react'

export const surfaceRegistry = {
  overview: { icon: Gauge, accent: 'neutral' },
  manual: { icon: Bot, accent: 'blue' },
  mapping: { icon: MapIcon, accent: 'teal' },
  navigation: { icon: Navigation, accent: 'blue' },
  behavior: { icon: Radar, accent: 'amber' },
  task: { icon: ClipboardList, accent: 'teal' },
  vision: { icon: Eye, accent: 'violet' },
}

export const groupLabels = {
  system: '系统',
  control: '控制',
  mapping: '建图',
  navigation: '导航',
  behavior: '行为',
}

export const layerRegistry = [
  { id: 'map', label: '地图', icon: MapIcon, defaultOn: true },
  { id: 'global', label: '全局代价', icon: ShieldAlert, defaultOn: false },
  { id: 'local', label: '局部代价', icon: Crosshair, defaultOn: true },
  { id: 'path', label: '路径', icon: Route, defaultOn: true },
  { id: 'scan', label: '雷达', icon: Radar, defaultOn: true },
]

export function featureMeta(feature) {
  return surfaceRegistry[feature?.surface] || surfaceRegistry.overview
}
