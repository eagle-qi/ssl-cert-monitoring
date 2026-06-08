import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { isAuthenticated, isAdmin } from './utils/auth';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Certificates from './pages/Certificates';
import Alerts from './pages/Alerts';
import Targets from './pages/Targets';
import AgentTargets from './pages/AgentTargets';
import CredentialManager from './pages/CredentialManager';
import Settings from './pages/Settings';
import Layout from './components/Layout';

function PrivateRoute({ children }: { children: React.ReactNode }) {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace />;
  }
  if (!isAdmin()) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <PrivateRoute>
              <Layout />
            </PrivateRoute>
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="certificates" element={<Certificates />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="targets" element={<AdminRoute><Targets /></AdminRoute>} />
          <Route path="agent-targets" element={<AdminRoute><AgentTargets /></AdminRoute>} />
          <Route path="credential-manager" element={<AdminRoute><CredentialManager /></AdminRoute>} />
          <Route path="settings" element={<AdminRoute><Settings /></AdminRoute>} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
