import { useMemo, useState } from 'react'
import { Box, Cable, CheckCircle2, CircleAlert, Container, Cpu, FileText, FlaskConical, Network, RadioTower, Router } from 'lucide-react'

const tabs = [
  { id: 'connection', label: '连接', icon: Cable },
  { id: 'components', label: '组件', icon: Box },
  { id: 'nodes', label: '节点', icon: Network },
  { id: 'logs', label: '日志', icon: FileText },
]

function levelClass(level) {
  if (level === 'OK' || level === true) return 'ok'
  if (level === 'WARN' || level === 'DEGRADED') return 'warn'
  if (level === 'ERROR' || level === false) return 'error'
  return 'neutral'
}

export default function StatusPanel({ activeFeature, health, bridge, connected, environment, telemetry, logs }) {
  const [tab, setTab] = useState('connection')
  const components = activeFeature?.components || []
  const nodes = useMemo(() => components.flatMap((component) => component.nodes.map((node) => ({ node, component: component.id }))), [components])
  return (
    <section className="status-panel">
      <div className="panel-tabs" role="tablist" aria-label="运行详情">
        {tabs.map(({ id, label, icon: Icon }) => <button type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'is-active' : ''} onClick={() => setTab(id)} key={id}><Icon size={15} />{label}</button>)}
      </div>
      <div className="status-content">
        {tab === 'connection' && (
          <div className="connection-list">
            <ConnectionRow icon={environment === 'demo' ? FlaskConical : Router} label="控制服务" value={environment === 'demo' ? '本机模拟' : connected ? 'Jetson 在线' : 'Jetson 断开'} ok={connected && environment !== 'demo'} warn={environment === 'demo'} />
            <ConnectionRow icon={Cpu} label="Docker 环境" value={Object.values(health?.targets || {}).some((target) => target.running) ? '容器可用' : '容器未运行'} ok={Object.values(health?.targets || {}).some((target) => target.running)} />
            {Object.entries(health?.hardware || {}).map(([id, item]) => <ConnectionRow icon={Cable} label={item.label || id} value={item.present ? item.detail || '已检测' : '未检测'} ok={item.present} key={id} />)}
            <ConnectionRow icon={RadioTower} label="ROS 中介" value={bridge?.available ? '已连接' : activeFeature?.id === 'IDLE' ? '待机未启动' : '未连接'} ok={bridge?.available} warn={activeFeature?.id === 'IDLE'} />
            <ConnectionRow icon={Network} label="底盘响应" value={telemetry?.voltage != null ? `${telemetry.voltage.toFixed(2)} V` : activeFeature?.id === 'IDLE' ? '待激活验证' : '未收到电压'} ok={telemetry?.voltage != null} warn={activeFeature?.id === 'IDLE'} />
          </div>
        )}
        {tab === 'components' && (
          <div className="component-list">
            {components.length === 0 && <div className="empty-list">无活动组件</div>}
            {components.map((component) => {
              const state = health?.processes?.[component.id]
              const running = state?.running
              const lifecycle = component.lifecycle_node ? health?.lifecycle?.[component.lifecycle_node] : null
              return (
                <div className="component-row" key={component.id}>
                  <span className={`row-icon ${levelClass(running)}`}>{running ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}</span>
                  <div><strong>{component.id}</strong><span><Container size={12} />{component.target}{state?.pid ? ` · PID ${state.pid}` : ''}</span></div>
                  <div className="row-tags"><i className={`tiny-status ${levelClass(running)}`}>{running ? 'RUN' : 'WAIT'}</i>{lifecycle && <i className={`tiny-status ${levelClass(lifecycle === 'active')}`}>{lifecycle}</i>}</div>
                </div>
              )
            })}
          </div>
        )}
        {tab === 'nodes' && (
          <div className="node-list">
            {nodes.length === 0 && <div className="empty-list">无预期节点</div>}
            {nodes.map(({ node, component }) => {
              const present = health?.nodes?.includes(node)
              return <div className="node-row" key={`${component}-${node}`}><RadioTower size={15} /><span>{node}</span><i className={`tiny-status ${levelClass(present)}`}>{present ? '在线' : '等待'}</i></div>
            })}
          </div>
        )}
        {tab === 'logs' && (
          <div className="log-list">
            {logs.length === 0 && <div className="empty-list">暂无事件</div>}
            {logs.map((log) => <div className={`log-row ${log.level.toLowerCase()}`} key={log.id}><time>{log.time.toLocaleTimeString('zh-CN', { hour12: false })}</time><i>{log.level}</i><span>{log.message}</span></div>)}
          </div>
        )}
      </div>
    </section>
  )
}

function ConnectionRow({ icon: Icon, label, value, ok = false, warn = false }) {
  const state = ok ? 'ok' : warn ? 'warn' : 'error'
  return <div className="connection-row"><span className={`row-icon ${state}`}><Icon size={15} /></span><div><strong>{label}</strong><span>{value}</span></div><i className={`connection-dot ${state}`} /></div>
}
