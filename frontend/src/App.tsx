import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './contexts/AuthContext';
import { LoginPage } from './components/LoginPage';
import { SignupPage } from './components/SignupPage';
import { Dashboard } from './components/Dashboard';
import { UploadPage } from './components/UploadPage';
import { ReviewInterface } from './components/ReviewInterface';
import { AccountPage } from './components/AccountPage';
import { Toaster } from './components/ui/sonner';

export default function App() {
  const { user, loading } = useAuth();

  // Show loading state while checking authentication
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-gray-600">Loading...</div>
      </div>
    );
  }

  return (
    <Routes>
      {/* Public routes */}
      <Route
        path="/login"
        element={user ? <Navigate to="/" replace /> : <LoginPage />}
      />
      <Route
        path="/signup"
        element={user ? <Navigate to="/" replace /> : <SignupPage />}
      />

      {/* Protected routes */}
      <Route
        path="/"
        element={user ? <Dashboard /> : <Navigate to="/login" replace />}
      />
      <Route
        path="/upload"
        element={user ? <UploadPage /> : <Navigate to="/login" replace />}
      />
      <Route
        path="/review/:sessionId"
        element={user ? <ReviewInterface /> : <Navigate to="/login" replace />}
      />
      <Route
        path="/account"
        element={user ? <AccountPage /> : <Navigate to="/login" replace />}
      />
    </Routes>
  );
}

// Add Toaster at the app level
export function AppWithToaster() {
  return (
    <>
      <App />
      <Toaster position="top-right" />
    </>
  );
}
