import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import ToastContainer from './components/Toast'
import Dashboard from './pages/Dashboard'
import Kanban from './pages/Kanban'
import Pending from './pages/Pending'
import Answers from './pages/Answers'
import Settings from './pages/Settings'

export default function App() {
  return (
    <>
      <Layout>
        <Routes>
          <Route path="/"         element={<Dashboard />} />
          <Route path="/kanban"   element={<Kanban />} />
          <Route path="/pending"  element={<Pending />} />
          <Route path="/answers"  element={<Answers />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </Layout>
      <ToastContainer />
    </>
  )
}
