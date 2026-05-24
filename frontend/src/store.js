import { create } from 'zustand'

export const useAppStore = create((set) => ({
  // Active filters on the Kanban board
  filters: { status: '', stage: '', platform: '' },
  setFilters: (filters) => set({ filters }),

  // Selected application for detail panel
  selectedAppId: null,
  setSelectedAppId: (id) => set({ selectedAppId: id }),

  // Toast notifications
  toasts: [],
  addToast: (msg, type = 'info') =>
    set((s) => ({
      toasts: [...s.toasts, { id: Date.now(), msg, type }],
    })),
  removeToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
