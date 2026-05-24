import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getPending, resolvePending, skipPending, rejectApp } from '../api'
import { useAppStore } from '../store'

function PendingCard({ item, onResolve, onSkip, onReject }) {
  const [answer, setAnswer] = useState(item.suggested_answer || '')
  const [saveToBank, setSaveToBank] = useState(true)

  return (
    <div className="card space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-white">{item.field_label || item.field_name}</p>
          <p className="text-xs text-gray-500 mt-0.5">App: {item.application_id.slice(0, 8)}</p>
        </div>
        <span className="badge bg-yellow-900 text-yellow-300 flex-shrink-0">
          {(item.confidence * 100).toFixed(0)}% confidence
        </span>
      </div>

      {item.question_text && (
        <p className="text-sm text-gray-400">{item.question_text}</p>
      )}

      <input
        className="input"
        placeholder="Your answer…"
        value={answer}
        onChange={e => setAnswer(e.target.value)}
      />

      <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
        <input
          type="checkbox"
          checked={saveToBank}
          onChange={e => setSaveToBank(e.target.checked)}
          className="accent-indigo-500"
        />
        Save to answer bank for future reuse
      </label>

      <div className="flex gap-2 pt-1">
        <button
          className="btn-primary flex-1"
          onClick={() => onResolve(item.id, answer, saveToBank)}
          disabled={!answer.trim()}
        >
          Resolve
        </button>
        <button className="btn-ghost border border-gray-700" onClick={() => onSkip(item.id)}>
          Skip field
        </button>
        <button
          className="btn bg-red-900 hover:bg-red-800 text-red-300 border border-red-800"
          onClick={() => onReject(item.id)}
        >
          Reject app
        </button>
      </div>
    </div>
  )
}

export default function Pending() {
  const qc = useQueryClient()
  const addToast = useAppStore((s) => s.addToast)

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['pending'],
    queryFn: getPending,
    refetchInterval: 10_000,
  })

  const resolveMut = useMutation({
    mutationFn: ({ id, answer, saveToBank }) => resolvePending(id, { answer, save_to_bank: saveToBank }),
    onSuccess: () => { qc.invalidateQueries(['pending']); addToast('Field resolved ✓', 'success') },
    onError:   () => addToast('Resolve failed', 'error'),
  })

  const skipMut = useMutation({
    mutationFn: (id) => skipPending(id),
    onSuccess: () => { qc.invalidateQueries(['pending']); addToast('Field skipped', 'info') },
  })

  const rejectMut = useMutation({
    mutationFn: (id) => rejectApp(id),
    onSuccess: () => { qc.invalidateQueries(['pending']); addToast('Application rejected', 'info') },
  })

  if (isLoading) return <p className="text-gray-500">Loading…</p>

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-white">Pending Review</h1>
        <span className="badge bg-yellow-900 text-yellow-300">{items.length} waiting</span>
      </div>

      {items.length === 0 ? (
        <div className="card text-center py-12 text-gray-500">
          🎉 No pending clarifications — all caught up!
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {items.map(item => (
            <PendingCard
              key={item.id}
              item={item}
              onResolve={(id, answer, saveToBank) => resolveMut.mutate({ id, answer, saveToBank })}
              onSkip={id => skipMut.mutate(id)}
              onReject={id => rejectMut.mutate(id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
