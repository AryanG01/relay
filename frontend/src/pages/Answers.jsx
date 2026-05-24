import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getAnswerBank, addAnswer, updateAnswer, deleteAnswer, testLookup } from '../api'
import { useAppStore } from '../store'

function AnswerRow({ row, onSave, onDelete }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal]     = useState(row.value)
  const [hint, setHint]   = useState(row.format_hint || '')

  if (!editing) {
    return (
      <div className="flex items-center gap-3 py-2 border-b border-gray-800 last:border-0">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-white truncate">{row.key}</p>
          <p className="text-xs text-gray-400 truncate">{row.value}</p>
        </div>
        <span className="text-xs text-gray-600 hidden sm:block">{row.format_hint}</span>
        <span className="text-xs text-gray-600">×{row.usage_count}</span>
        <button className="btn-ghost text-xs" onClick={() => setEditing(true)}>Edit</button>
        <button className="btn text-xs bg-red-900 hover:bg-red-800 text-red-300" onClick={() => onDelete(row.id)}>Del</button>
      </div>
    )
  }

  return (
    <div className="py-2 border-b border-gray-800 space-y-2">
      <p className="text-sm text-gray-400 font-mono">{row.key}</p>
      <input className="input" value={val}  onChange={e => setVal(e.target.value)}  placeholder="Value" />
      <input className="input" value={hint} onChange={e => setHint(e.target.value)} placeholder="Format hint (text/number/yesno/range)" />
      <div className="flex gap-2">
        <button className="btn-primary text-xs" onClick={() => { onSave(row.id, val, hint); setEditing(false) }}>Save</button>
        <button className="btn-ghost text-xs" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}

export default function Answers() {
  const qc = useQueryClient()
  const addToast = useAppStore((s) => s.addToast)

  const [newKey,   setNewKey]   = useState('')
  const [newVal,   setNewVal]   = useState('')
  const [newHint,  setNewHint]  = useState('')
  const [testLabel, setTestLabel] = useState('')
  const [testResult, setTestResult] = useState(null)

  const { data: rows = [], isLoading } = useQuery({ queryKey: ['answer-bank'], queryFn: getAnswerBank })

  const addMut = useMutation({
    mutationFn: () => addAnswer({ key: newKey.trim(), value: newVal.trim(), format_hint: newHint || null }),
    onSuccess: () => {
      qc.invalidateQueries(['answer-bank'])
      addToast('Answer added', 'success')
      setNewKey(''); setNewVal(''); setNewHint('')
    },
    onError: (e) => addToast(e.response?.data?.detail || 'Add failed', 'error'),
  })

  const saveMut = useMutation({
    mutationFn: ({ id, value, format_hint }) => updateAnswer(id, { key: rows.find(r => r.id === id)?.key, value, format_hint }),
    onSuccess: () => { qc.invalidateQueries(['answer-bank']); addToast('Updated', 'success') },
  })

  const delMut = useMutation({
    mutationFn: (id) => deleteAnswer(id),
    onSuccess: () => { qc.invalidateQueries(['answer-bank']); addToast('Deleted', 'info') },
  })

  const handleTestLookup = async () => {
    try {
      const r = await testLookup({ field_label: testLabel, field_type: 'text' })
      setTestResult(r)
    } catch {
      addToast('Lookup failed', 'error')
    }
  }

  if (isLoading) return <p className="text-gray-500">Loading…</p>

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-white">Answer Bank</h1>

      {/* Add new */}
      <div className="card space-y-3">
        <h2 className="text-sm font-medium text-gray-400">Add New Answer</h2>
        <div className="grid sm:grid-cols-3 gap-2">
          <input className="input" placeholder="key (e.g. phone_number)" value={newKey}  onChange={e => setNewKey(e.target.value)} />
          <input className="input" placeholder="value"                    value={newVal}  onChange={e => setNewVal(e.target.value)} />
          <input className="input" placeholder="format_hint (optional)"  value={newHint} onChange={e => setNewHint(e.target.value)} />
        </div>
        <button className="btn-primary" onClick={() => addMut.mutate()} disabled={!newKey || !newVal || addMut.isPending}>
          Add
        </button>
      </div>

      {/* Test lookup */}
      <div className="card space-y-3">
        <h2 className="text-sm font-medium text-gray-400">Test Lookup</h2>
        <div className="flex gap-2">
          <input className="input" placeholder="Field label to test…" value={testLabel} onChange={e => setTestLabel(e.target.value)} />
          <button className="btn-primary flex-shrink-0" onClick={handleTestLookup} disabled={!testLabel}>Test</button>
        </div>
        {testResult && (
          <div className="bg-gray-800 rounded p-3 text-xs font-mono text-gray-300 space-y-1">
            <p><span className="text-gray-500">match_type:</span> {testResult.match_type}</p>
            <p><span className="text-gray-500">confidence:</span> {testResult.confidence}</p>
            <p><span className="text-gray-500">value:</span> {testResult.value || '(none)'}</p>
          </div>
        )}
      </div>

      {/* Answer list */}
      <div className="card">
        <h2 className="text-sm font-medium text-gray-400 mb-3">{rows.length} stored answers</h2>
        {rows.map(row => (
          <AnswerRow
            key={row.id}
            row={row}
            onSave={(id, value, format_hint) => saveMut.mutate({ id, value, format_hint })}
            onDelete={id => delMut.mutate(id)}
          />
        ))}
      </div>
    </div>
  )
}
