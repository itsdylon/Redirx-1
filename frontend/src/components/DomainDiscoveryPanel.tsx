import { useState } from 'react';
import { usePostHog } from '@posthog/react';
import { CheckCircle2, Globe, Loader2 } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { discoverSite, type DiscoveryResponse } from '../api/discovery';

const METHOD_LABELS: Record<string, string> = {
  sitemap: 'sitemap',
  wordpress_api: 'WordPress API',
  shopify_api: 'Shopify API',
  crawl: 'site crawl',
};

const GENERATOR_LABELS: Record<string, string> = {
  wordpress: 'WordPress',
  shopify: 'Shopify',
  webflow: 'Webflow',
  squarespace: 'Squarespace',
  wix: 'Wix',
};

interface DomainDiscoveryPanelProps {
  side: 'old' | 'new';
  label: string;
  onDiscovered: (side: 'old' | 'new', result: DiscoveryResponse | null) => void;
}

/**
 * One half of the paste-two-domains ingestion flow: a domain input that
 * discovers the site's pages (sitemap -> CMS API -> crawl) and reports the
 * result upward as if a URL file had been uploaded.
 */
export function DomainDiscoveryPanel({ side, label, onDiscovered }: DomainDiscoveryPanelProps) {
  const posthog = usePostHog();
  const [domain, setDomain] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DiscoveryResponse | null>(null);

  const handleDiscover = async () => {
    const trimmed = domain.trim();
    if (!trimmed || isLoading) return;

    setIsLoading(true);
    setError(null);
    posthog?.capture('domain_discovery_started', { side, domain: trimmed });

    try {
      const response = await discoverSite(trimmed);
      setResult(response);
      onDiscovered(side, response);
      posthog?.capture('domain_discovery_completed', {
        side,
        domain: trimmed,
        count: response.count,
        method: response.method,
        generator: response.generator,
        truncated: response.truncated,
        duration_ms: response.duration_ms,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not scan this domain.';
      setError(message);
      setResult(null);
      onDiscovered(side, null);
      posthog?.capture('domain_discovery_failed', { side, domain: trimmed, error: message });
    } finally {
      setIsLoading(false);
    }
  };

  const handleClear = () => {
    setResult(null);
    setError(null);
    onDiscovered(side, null);
  };

  if (result) {
    const methodLabel = METHOD_LABELS[result.method] || result.method;
    const generatorLabel = result.generator ? GENERATOR_LABELS[result.generator] : null;
    return (
      <div className="border border-emerald-500/40 bg-emerald-500/5 p-5 rounded-lg">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="h-5 w-5 text-emerald-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-foreground">
                {new URL(result.root_url).hostname}
                {generatorLabel && (
                  <span className="ml-2 text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded">
                    {generatorLabel}
                  </span>
                )}
              </p>
              <p className="text-sm text-muted-foreground mt-1">
                {result.count.toLocaleString()} pages found via {methodLabel}
              </p>
              {result.truncated && (
                <p className="text-xs text-muted-foreground mt-1">
                  Site has {result.total_found.toLocaleString()}+ pages — capped at{' '}
                  {result.max_urls.toLocaleString()} on your plan.
                </p>
              )}
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={handleClear}>
            Change
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="border border-dashed border-border bg-background p-5 rounded-lg">
      <div className="flex items-center gap-2 mb-3">
        <Globe className="h-4 w-4 text-muted-foreground" />
        <p className="text-sm font-medium text-foreground">{label}</p>
      </div>
      <div className="flex gap-2">
        <Input
          type="text"
          placeholder={side === 'old' ? 'old-site.com' : 'new-site.com'}
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              handleDiscover();
            }
          }}
          disabled={isLoading}
          aria-label={`${label} domain`}
        />
        <Button onClick={handleDiscover} disabled={!domain.trim() || isLoading}>
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Scanning...
            </>
          ) : (
            'Find Pages'
          )}
        </Button>
      </div>
      {isLoading && (
        <p className="text-xs text-muted-foreground mt-2">
          Checking sitemap, platform APIs, and site links — usually a few seconds.
        </p>
      )}
      {error && <p className="text-sm text-destructive mt-2">{error}</p>}
    </div>
  );
}
