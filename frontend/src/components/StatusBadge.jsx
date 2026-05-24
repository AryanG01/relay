const COLORS = {
  discovered:            'bg-gray-700 text-gray-300',
  queued:                'bg-blue-900 text-blue-300',
  tailoring:             'bg-purple-900 text-purple-300',
  pending_clarification: 'bg-yellow-900 text-yellow-300',
  applying:              'bg-indigo-900 text-indigo-300',
  applied:               'bg-green-900 text-green-300',
  failed:                'bg-red-900 text-red-300',
  expired:               'bg-gray-800 text-gray-500',
  skipped:               'bg-gray-800 text-gray-500',
}

export default function StatusBadge({ status }) {
  const cls = COLORS[status] || 'bg-gray-700 text-gray-300'
  return (
    <span className={`badge ${cls}`}>
      {status?.replace(/_/g, ' ')}
    </span>
  )
}
