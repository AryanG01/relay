import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getQueueStats, getApplications, pauseQueue, resumeQueue } from '../api'
import { useAppStore } from '../store'
import StatusBadge from '../components/StatusBadge'

const STATUSES = ['discovered','queued','tailoring','pending_clarification','applying','applied','failed','skipped','expired']

function StatCard({ label, value, sub }) {
  return (
    <div className="card">
      <div className="text-2xl font-bold text-white">{value ?? '—'}</div>
      <div className="text-sm text-gray-400 mt-0.5">{label}</div>
      {sub && <div className="text-xs text-gray-600 mt-1">{sub}</div>}
    </div>
  )
}

export default function Dashboard() {
  const qc = useQueryClient()
  const addToast = useAppStore((s) => s.addToast)

  const { data: stats } = useQuery({ queryKey: ['queue-stats'], queryFn: getQueueStats, refetchInterval: 10_000 })
  const { data: apps = [] } = useQuery({ queryKey: ['applications'], queryFn: () => getApplications(), refetchInterval: 15_000 })

  const pauseMut  = useMutation({ mutationFn: pauseQueue,  onSuccess: () => { qc.invalidateQueries(['queue-stats']); addToast('Queue paused', 'info') } })
  const resumeMut = useMutation({ mutationFn: resumeQueue, onSuccess: () => { qc.invalidateQueries(['queue-stats']); addToast('Queue resumed', 'success') } })

  // Count by status
  const byStatus = STATUSES.reduce((acc, s) => {
    acc[s] = apps.filter(a => a.status === s).length
    return acc
  }, {})

  const applied  = apps.filter(a => a.status === 'applied').length
  const pending  = apps.filter(a => a.status === 'pending_clarification').length
  const failed   = apps.filter(a => a.status === 'failed').length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-white">Overview</h1>
        <div className="flex gap-2">
          {stats?.paused ? (
            <button className="btn-primary" onClick={() => resumeMut.mutate()} disabled={resumeMut.isPending}>
              ▶ Resume Queue
            </button>
          ) : (
            <button className="btn-ghost border border-gray-700" onClick={() => pauseMut.mutate()} disabled={pauseMut.isPending}>
              ⏸ Pause Queue
            </button>
          )}
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Queued" value={stats?.total_queued} sub={`cap: ${stats?.daily_cap ?? '—'}/day`} />
        <StatCard label="Today" value={stats?.daily_dispatched} sub="dispatched" />
        <StatCard label="Applied (total)" value={applied} />
        <StatCard label="Needs Review" value={pending} />
      </div>

      {/* Status breakdown */}
      <div className="card">
        <h2 className="text-sm font-medium text-gray-400 mb-3">Applications by Status</h2>
        <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
          {STATUSES.map(s => (
            <div key={s} className="text-center">
              <div className="text-lg font-semibold text-white">{byStatus[s] || 0}</div>
              <StatusBadge status={s} />
            </div>
          ))}
        </div>
      </div>

      {/* Recent applications */}
      <div className="card">
        <h2 className="text-sm font-medium text-gray-400 mb-3">Recent Applications</h2>
        <div className="space-y-2">
          {apps.slice(0, 8).map(app => (
            <div key={app.id} className="flex items-center justify-between py-1.5 border-b border-gray-800 last:border-0">
              <div>
                <span className="text-sm text-white font-medium">{app.company}</span>
                <span className="text-xs text-gray-500 ml-2">{app.role_title}</span>
              </div>
              <div className="flex items-center gap-2">
                {app.match_score != null && (
                  <span className="text-xs text-indigo-400">{app.match_score.toFixed(0)}%</span>
                )}
                <StatusBadge status={app.status} />
              </div>
            </div>
          ))}
          {apps.length === 0 && (
            <p className="text-sm text-gray-600 text-center py-4">No applications yet</p>
          )}
        </div>
      </div>

      {/* Queue paused warning */}
      {stats?.paused && (
        <div className="bg-yellow-900/30 border border-yellow-700 rounded-lg px-4 py-3 text-yellow-300 text-sm">
          ⚠️ Queue is paused — no new applications will be dispatched until resumed.
        </div>
      )}
    </div>
  )
}
