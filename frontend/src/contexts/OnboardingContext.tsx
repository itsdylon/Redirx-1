import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  ReactNode,
} from 'react';
import {
  createOnboardingSampleSession,
  getOnboarding,
  OnboardingPath,
  OnboardingResponse,
  OnboardingStep,
  SampleSessionResponse,
  updateOnboarding,
} from '../api/onboarding';
import { useAuth } from './AuthContext';

interface OnboardingContextType {
  onboarding: OnboardingResponse | null;
  loading: boolean;
  error: string | null;
  entryModalOpen: boolean;
  setEntryModalOpen: (open: boolean) => void;
  refreshOnboarding: () => Promise<OnboardingResponse | null>;
  startOnboarding: () => Promise<OnboardingResponse | null>;
  selectPath: (path: OnboardingPath) => Promise<OnboardingResponse | null>;
  completeStep: (step: OnboardingStep) => Promise<OnboardingResponse | null>;
  dismissOnboarding: () => Promise<OnboardingResponse | null>;
  completeOnboarding: () => Promise<OnboardingResponse | null>;
  resetOnboarding: () => Promise<OnboardingResponse | null>;
  createSampleSession: () => Promise<SampleSessionResponse>;
  isStepCompleted: (step: OnboardingStep) => boolean;
}

const OnboardingContext = createContext<OnboardingContextType | undefined>(undefined);

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const { user, loading: authLoading } = useAuth();
  const [onboarding, setOnboarding] = useState<OnboardingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [entryModalOpen, setEntryModalOpen] = useState(false);

  const refreshOnboarding = useCallback(async (): Promise<OnboardingResponse | null> => {
    if (!user) {
      setOnboarding(null);
      setLoading(false);
      return null;
    }

    setLoading(true);
    setError(null);

    try {
      const data = await getOnboarding();
      setOnboarding(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load onboarding state');
      return null;
    } finally {
      setLoading(false);
    }
  }, [user]);

  const applyUpdate = useCallback(async (payload: Parameters<typeof updateOnboarding>[0]) => {
    if (!user) return null;
    setError(null);
    try {
      const data = await updateOnboarding(payload);
      setOnboarding(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update onboarding state');
      return null;
    }
  }, [user]);

  const createSampleSession = useCallback(async (): Promise<SampleSessionResponse> => {
    setError(null);
    try {
      return await createOnboardingSampleSession();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create sample tutorial session');
      throw err;
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      setOnboarding(null);
      setEntryModalOpen(false);
      setError(null);
      setLoading(false);
      return;
    }
    refreshOnboarding();
  }, [authLoading, user, refreshOnboarding]);

  const contextValue = useMemo<OnboardingContextType>(() => {
    return {
      onboarding,
      loading,
      error,
      entryModalOpen,
      setEntryModalOpen,
      refreshOnboarding,
      startOnboarding: () => applyUpdate({ action: 'start' }),
      selectPath: (path) => applyUpdate({ action: 'select_path', path }),
      completeStep: (step) => applyUpdate({ action: 'complete_step', step }),
      dismissOnboarding: () => applyUpdate({ action: 'dismiss' }),
      completeOnboarding: () => applyUpdate({ action: 'complete' }),
      resetOnboarding: () => applyUpdate({ action: 'reset' }),
      createSampleSession,
      isStepCompleted: (step) => !!onboarding?.onboarding_state?.steps?.[step]?.completed,
    };
  }, [applyUpdate, createSampleSession, entryModalOpen, error, loading, onboarding, refreshOnboarding]);

  return (
    <OnboardingContext.Provider value={contextValue}>
      {children}
    </OnboardingContext.Provider>
  );
}

export function useOnboarding() {
  const context = useContext(OnboardingContext);
  if (!context) {
    throw new Error('useOnboarding must be used within an OnboardingProvider');
  }
  return context;
}
