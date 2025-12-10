import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './contexts/AuthContext';
import { LoginPage } from './components/LoginPage';
import { SignupPage } from './components/SignupPage';
import { MainApp } from './components/MainApp';
import { AccountPage } from './components/AccountPage';

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
        path="/account"
        element={user ? <AccountPage /> : <Navigate to="/login" replace />}
      />
      <Route
        path="/*"
        element={user ? <MainApp /> : <Navigate to="/login" replace />}
      />
    </Routes>
  );
}
