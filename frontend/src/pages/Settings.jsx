import { useQuery } from '@tanstack/react-query'
import { getQueueStats, getAutomationStatus, getHealth } from '../api'

function Row({ label, value }) {
  return (
    <div className="flex justify-between items-center py-2 border-b border-gray-800 last:border-0">
      <span className="text-sm text-gray-400">{label}</span>
      <span className="text-sm text-white font-mono">{String(value ?? '—')}</span>
    </div>
  )
}

export default function Settings() {
  const { data: stats }  = useQuery({ queryKey: ['queue-stats'], queryFn: getQueueStats })
  const { data: auto }   = useQuery({ queryKey: ['auto-status'], queryFn: getAutomationStatus, retry: false })
  const { data: health } = useQuery({ queryKey: ['health'],      queryFn: getHealth,           retry: false })

  return (
    <div className="space-y-6 max-w-xl">
      <h1 className="text-xl font-semibold text-white">Settings & Status</h1>

      <div className="card">
        <h2 className="text-sm font-medium text-gray-400 mb-3">API Status</h2>
        <Row label="Backend"       value={health ? '🟢 online' : '🔴 offline'} />
        <Row label="Service"       value={health?.service} />
      </div>

      <div className="card">
        <h2 className="text-sm font-medium text-gray-400 mb-3">Queue Config</h2>
        <Row label="Daily cap"          value={stats?.daily_cap} />
        <Row label="Dispatched today"   value={stats?.daily_dispatched} />
        <Row label="Currently queued"   value={stats?.total_queued} />
        <Row label="Queue paused"       value={stats?.paused ? 'Yes ⚠️' : 'No'} />
      </div>

      <div className="card">
        <h2 className="text-sm font-medium text-gray-400 mb-3">Automation Agent</h2>
        <Row label="Enabled"            value={auto?.enabled ? 'Yes' : 'No'} />
        <Row label="In dispatch window" value={auto?.in_window ? 'Yes' : 'No'} />
        <Row label="Daily dispatched"   value={auto?.daily_dispatched} />
        <Row label="Daily cap"          value={auto?.daily_cap} />
      </div>

      <div className="card">
        <h2 className="text-sm font-medium text-gray-400 mb-3">Quick Start</h2>
        <div className="text-xs text-gray-500 space-y-1 font-mono">
          <p># Start backend</p>
          <p className="text-gray-300">uvicorn backend.main:app --reload --port 8000</p>
          <p className="mt-2"># Start local dispatch agent</p>
          <p className="text-gray-300">python3 scripts/local_agent.py</p>
          <p className="mt-2"># Run full pipeline test</p>
          <p className="text-gray-300">python3 scripts/test_pipeline.py "your JD text"</p>
        </div>
      </div>
    </div>
  )
}
