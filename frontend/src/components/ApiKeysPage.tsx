import { DashboardLayout } from './DashboardLayout';
import { ToolLayout } from './ToolLayout';
import { ApiKeysPanel } from './ApiKeysPanel';
import { useAuth } from '../contexts/AuthContext';
import { isEnterprisePlan } from '../lib/plans';

/**
 * Standalone page for issuing API keys.
 *
 * Exists as its own route rather than only as a Settings tab because Settings
 * is enterprise-only, while a free account can drive Quick Match over the API.
 * This is also the single URL that documentation and `llms.txt` can point any
 * account at without bouncing them to /quick-match.
 */
export function ApiKeysPage() {
  const { user } = useAuth();
  const Layout = isEnterprisePlan(user?.plan) ? DashboardLayout : ToolLayout;

  return (
    <Layout title="API keys">
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-8">
        <div>
          <h1 className="text-2xl font-semibold text-foreground">API keys</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Run a migration from an agent or a script, without a browser.
          </p>
        </div>

        <ApiKeysPanel />
      </div>
    </Layout>
  );
}
