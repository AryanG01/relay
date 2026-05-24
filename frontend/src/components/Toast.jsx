import { useEffect } from 'react'
import { useAppStore } from '../store'

const TYPE_CLS = {
  info:    'bg-gray-800 border-gray-700 text-gray-200',
  success: 'bg-green-900 border-green-700 text-green-200',
  error:   'bg-red-900 border-red-700 text-red-200',
}

function Toast({ id, msg, type }) {
  const removeToast = useAppStore((s) => s.removeToast)
  useEffect(() => {
    const t = setTimeout(() => removeToast(id), 3500)
    return () => clearTimeout(t)
  }, [id, removeToast])

  return (
    <div
      className={`border rounded px-4 py-2 text-sm shadow-lg cursor-pointer ${TYPE_CLS[type] || TYPE_CLS.info}`}
      onClick={() => removeToast(id)}
    >
      {msg}
    </div>
  )
}

export default function ToastContainer() {
  const toasts = useAppStore((s) => s.toasts)
  return (
    <div className="fixed bottom-4 right-4 flex flex-col gap-2 z-50">
      {toasts.map((t) => (
        <Toast key={t.id} {...t} />
      ))}
    </div>
  )
}
