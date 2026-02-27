import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider } from "next-themes";
import { PostHogProvider } from "@posthog/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./contexts/AuthContext";
import { AppWithToaster } from "./App.tsx";
import { OnboardingProvider } from "./contexts/OnboardingContext";
import { appQueryClient } from "./queries/queryClient";
import "./styles/globals.css";

const posthogOptions = {
  api_host: import.meta.env.VITE_PUBLIC_POSTHOG_HOST,
  defaults: "2026-01-30",
} as const;

createRoot(document.getElementById("root")!).render(
  <PostHogProvider
    apiKey={import.meta.env.VITE_PUBLIC_POSTHOG_KEY}
    options={posthogOptions}
  >
    <BrowserRouter>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <QueryClientProvider client={appQueryClient}>
          <AuthProvider>
            <OnboardingProvider>
              <AppWithToaster />
            </OnboardingProvider>
          </AuthProvider>
        </QueryClientProvider>
      </ThemeProvider>
    </BrowserRouter>
  </PostHogProvider>
);
