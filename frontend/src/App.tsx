import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './contexts/AuthContext';
import { LoginPage } from './components/LoginPage';
import { SignupPage } from './components/SignupPage';
import { AuthCallback } from './components/AuthCallback';
import { Dashboard } from './components/Dashboard';
import { AllProjects } from './components/AllProjects';
import { UploadPage } from './components/UploadPage';
import { ReviewInterface } from './components/ReviewInterface';
import { AccountPage } from './components/AccountPage';
import { Settings } from './components/Settings';
import { PricingPage } from './components/PricingPage';
import { DemoPage } from './components/DemoPage';
import { Toaster } from './components/ui/sonner';

export default function App() {
  const { user, loading } = useAuth();

  // Show loading state while checking authentication
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-muted-foreground">Loading...</div>
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
      <Route
        path="/auth/callback"
        element={<AuthCallback />}
      />
      <Route
        path="/demo"
        element={<DemoPage />}
      />

      {/* Protected routes */}
      <Route
        path="/"
        element={user ? <Dashboard /> : <Navigate to="/login" replace />}
      />
      <Route
        path="/dashboard"
        element={user ? <Dashboard /> : <Navigate to="/login" replace />}
      />
      <Route
        path="/projects"
        element={user ? <AllProjects /> : <Navigate to="/login" replace />}
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
        path="/settings"
        element={user ? <Settings /> : <Navigate to="/login" replace />}
      />
      <Route
        path="/pricing"
        element={user ? <PricingPage /> : <Navigate to="/login" replace />}
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
