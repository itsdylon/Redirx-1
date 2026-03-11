import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
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
import { QuickMatchLandingPage } from './components/QuickMatchLandingPage';
import { Toaster } from './components/ui/sonner';
import { isEnterprisePlan } from './lib/plans';
import {
  ROUTES,
  canAccessDashboard,
  canAccessPricing,
  canAccessQuickMatch,
  canAccessSettingsAndAccount,
  canAccessUpload,
  getAuthedHomeRoute,
} from './routes';

export default function App() {
  const { user, loading } = useAuth();
  const location = useLocation();
  const authedHome = getAuthedHomeRoute(user?.plan);
  const pricingSourceSessionId = new URLSearchParams(location.search).get('source_session_id');
  const reviewLayoutVariant = isEnterprisePlan(user?.plan) ? 'dashboard' : 'tool';

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
        path={ROUTES.login}
        element={user ? <Navigate to={authedHome} replace /> : <LoginPage />}
      />
      <Route
        path={ROUTES.signup}
        element={user ? <Navigate to={authedHome} replace /> : <SignupPage />}
      />
      <Route
        path={ROUTES.authCallback}
        element={<AuthCallback />}
      />
      <Route
        path={ROUTES.quickMatch}
        element={
          user
            ? (
              canAccessQuickMatch(user?.plan)
                ? <QuickMatchLandingPage />
                : <Navigate to={`${ROUTES.upload}?mode=url_only`} replace />
            )
            : <QuickMatchLandingPage />
        }
      />
      <Route
        path={ROUTES.demo}
        element={<DemoPage />}
      />

      {/* Protected routes */}
      <Route
        path={ROUTES.root}
        element={user ? <Navigate to={authedHome} replace /> : <Navigate to={ROUTES.quickMatch} replace />}
      />
      <Route
        path={ROUTES.dashboard}
        element={
          user
            ? (canAccessDashboard(user?.plan) ? <Dashboard /> : <Navigate to={ROUTES.quickMatch} replace />)
            : <Navigate to={ROUTES.login} replace />
        }
      />
      <Route
        path={ROUTES.projects}
        element={user ? <AllProjects /> : <Navigate to={ROUTES.login} replace />}
      />
      <Route
        path={ROUTES.upload}
        element={
          user
            ? (canAccessUpload(user?.plan) ? <UploadPage /> : <Navigate to={ROUTES.quickMatch} replace />)
            : <Navigate to={ROUTES.login} replace />
        }
      />
      <Route
        path={ROUTES.review}
        element={user ? <ReviewInterface layoutVariant={reviewLayoutVariant} /> : <Navigate to={ROUTES.login} replace />}
      />
      <Route
        path={ROUTES.settings}
        element={
          user
            ? (
              canAccessSettingsAndAccount(user?.plan)
                ? <Settings />
                : <Navigate to={ROUTES.quickMatch} replace />
            )
            : <Navigate to={ROUTES.login} replace />
        }
      />
      <Route
        path={ROUTES.pricing}
        element={
          user
            ? (
              canAccessPricing(user?.plan, pricingSourceSessionId)
                ? <PricingPage />
                : <Navigate to={ROUTES.quickMatch} replace />
            )
            : <Navigate to={ROUTES.login} replace />
        }
      />
      <Route
        path={ROUTES.account}
        element={
          user
            ? (
              canAccessSettingsAndAccount(user?.plan)
                ? <AccountPage />
                : <Navigate to={ROUTES.quickMatch} replace />
            )
            : <Navigate to={ROUTES.login} replace />
        }
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
