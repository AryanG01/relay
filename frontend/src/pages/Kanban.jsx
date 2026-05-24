import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getApplications, updateStage, retryApplication } from '../api'
import { useAppStore } from '../store'
import StatusBadge from '../components/StatusBadge'

const COLUMNS = [
  { key: 'applied',    label: '✅ Applied' },
  { key: 'screening',  label: '📋 Screening' },
  { key: 'oa',         label: '🧪 OA' },
  { key: 'phone',      label: '📞 Phone' },
  { key: 'interview_1',label: '🎤 Interview' },
  { key: 'offer',      label: '🎉 Offer' },
  { key: 'rejected',   label: '❌ Rejected' },
  { key: 'ghosted',    label: '👻 Ghosted' },
]

const STAGE_OPTIONS = [
  'applied','screening','oa','phone',
  'interview_1','interview_2','interview_3',
  'offer','rejected','ghosted','withdrawn',
]

function AppCard({ app, onStageChange }) {
  return (
    <div className="bg-gray-850 border border-gray-700 rounded p-3 space-y-1.5 hover:border-indigo-600 transition-colors">
      <div className="text-sm font-medium text-white truncate">{app.company}</div>
      <div className="text-xs text-gray-400 truncate">{app.role_title}</div>
      <div className="flex items-center justify-between pt-1">
        {app.match_score != null && (
          <span className="text-xs text-indigo-400">{app.match_score.toFixed(0)}% match</span>
        )}
        <select
          className="text-xs bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-gray-300 ml-auto"
          value={app.stage}
          onChange={e => onStageChange(app.id, e.target.value)}
        >
          {STAGE_OPTIONS.map(s => (
            <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
          ))}
        </select>
      </div>
      <div className="text-xs text-gray-600 truncate">{app.source_platform}</div>
    </div>
  )
}

export default function Kanban() {
  const qc = useQueryClient()
  const addToast = useAppStore((s) => s.addToast)

  // Show only APPLIED applications for the Kanban view
  const { data: apps = [], isLoading } = useQuery({
    queryKey: ['applications', { status: 'applied' }],
    queryFn: () => getApplications({ status: 'applied' }),
    refetchInterval: 20_000,
  })

  const stageMut = useMutation({
    mutationFn: ({ id, stage }) => updateStage(id, { stage }),
    onSuccess: () => {
      qc.invalidateQueries(['applications'])
      addToast('Stage updated', 'success')
    },
    onError: () => addToast('Failed to update stage', 'error'),
  })

  const byStage = COLUMNS.reduce((acc, col) => {
    acc[col.key] = apps.filter(a => a.stage === col.key)
    return acc
  }, {})

  if (isLoading) return <p className="text-gray-500">Loading…</p>

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-white">Kanban Board</h1>
      <p className="text-sm text-gray-500">{apps.length} applied applications</p>

      <div className="flex gap-3 overflow-x-auto pb-2">
        {COLUMNS.map(col => (
          <div key={col.key} className="flex-shrink-0 w-52">
            <div className="text-xs font-medium text-gray-400 mb-2 flex items-center justify-between">
              <span>{col.label}</span>
              <span className="bg-gray-800 px-1.5 rounded">{byStage[col.key]?.length || 0}</span>
            </div>
            <div className="space-y-2 min-h-32">
              {(byStage[col.key] || []).map(app => (
                <AppCard
                  key={app.id}
                  app={app}
                  onStageChange={(id, stage) => stageMut.mutate({ id, stage })}
                />
              ))}
              {!byStage[col.key]?.length && (
                <div className="border border-dashed border-gray-800 rounded h-16 flex items-center justify-center">
                  <span className="text-xs text-gray-700">empty</span>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
