import axios from 'axios'

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const api = axios.create({ baseURL: BASE, timeout: 15000 })

// Applications
export const getApplications = (params) => api.get('/api/applications', { params }).then(r => r.data)
export const getApplication  = (id)     => api.get(`/api/applications/${id}`).then(r => r.data)
export const createApplication = (body) => api.post('/api/applications', body).then(r => r.data)
export const updateStage     = (id, body) => api.post(`/api/applications/${id}/stage`, body).then(r => r.data)
export const retryApplication = (id)    => api.post(`/api/applications/${id}/retry`).then(r => r.data)
export const getHistory      = (id)     => api.get(`/api/applications/${id}/history`).then(r => r.data)

// Queue
export const getQueueStats = ()   => api.get('/api/queue/stats').then(r => r.data)
export const pauseQueue    = ()   => api.post('/api/queue/pause').then(r => r.data)
export const resumeQueue   = ()   => api.post('/api/queue/resume').then(r => r.data)
export const enqueueApp    = (id) => api.post(`/api/queue/enqueue/${id}`).then(r => r.data)

// Answer bank
export const getAnswerBank    = ()           => api.get('/api/answer-bank').then(r => r.data)
export const addAnswer        = (body)       => api.post('/api/answer-bank', body).then(r => r.data)
export const updateAnswer     = (id, body)   => api.put(`/api/answer-bank/${id}`, body).then(r => r.data)
export const deleteAnswer     = (id)         => api.delete(`/api/answer-bank/${id}`)
export const testLookup       = (body)       => api.post('/api/answer-bank/test-lookup', body).then(r => r.data)

// Pending clarifications
export const getPending       = ()           => api.get('/api/pending').then(r => r.data)
export const resolvePending   = (id, body)   => api.post(`/api/pending/${id}/resolve`, body).then(r => r.data)
export const skipPending      = (id)         => api.post(`/api/pending/${id}/skip`).then(r => r.data)
export const rejectApp        = (id)         => api.post(`/api/pending/${id}/reject-app`).then(r => r.data)

// Automation
export const getAutomationStatus = () => api.get('/api/automation/status').then(r => r.data)

// Health
export const getHealth = () => api.get('/health').then(r => r.data)
