import type { DiscoveredEntry, DiscoverySummary } from '../api/discovery';

/**
 * Normalized shape the risk panel renders, so the same component works whether
 * the numbers came from client-side discovery (before matching) or from the
 * results endpoint (after). That's what makes the placement reversible.
 */
export interface TrafficRisk {
  domain: string;
  total: number;
  withTraffic: number;
  noRecordedTraffic: number;
  lowPriority: number;
  /** Smallest set of URLs carrying `shareTarget` of all clicks. */
  topUrlCount: number;
  totalClicks: number;
  totalImpressions: number;
  shareTarget: number;
  weightMetric: 'clicks' | 'impressions';
  hasGsc: boolean;
  topUrls: Array<{ url: string; clicks: number; impressions: number; kind?: string }>;
}

/**
 * How few URLs account for most of the traffic.
 *
 * Falls back to impressions when a site has no clicks at all — a site that
 * ranks but doesn't convert still has something to lose.
 */
export function computeRisk(
  domain: string,
  entries: DiscoveredEntry[],
  summary: DiscoverySummary | undefined,
  shareTarget = 0.8
): TrafficRisk {
  const totalClicks = entries.reduce((n, e) => n + (e.clicks || 0), 0);
  const totalImpressions = entries.reduce((n, e) => n + (e.impressions || 0), 0);
  const useClicks = totalClicks > 0;
  const weight = (e: DiscoveredEntry) => (useClicks ? e.clicks : e.impressions) || 0;
  const totalWeight = useClicks ? totalClicks : totalImpressions;

  const ranked = [...entries].filter((e) => weight(e) > 0).sort((a, b) => weight(b) - weight(a));

  let running = 0;
  let topUrlCount = 0;
  for (const e of ranked) {
    if (totalWeight > 0 && running / totalWeight >= shareTarget) break;
    running += weight(e);
    topUrlCount += 1;
  }

  return {
    domain,
    total: summary?.total ?? entries.length,
    withTraffic: summary?.with_traffic ?? ranked.length,
    noRecordedTraffic: summary?.no_recorded_traffic ?? entries.length - ranked.length,
    lowPriority: (summary as { low_priority?: number } | undefined)?.low_priority ?? 0,
    topUrlCount,
    totalClicks,
    totalImpressions,
    shareTarget: Math.round(shareTarget * 100),
    weightMetric: useClicks ? 'clicks' : 'impressions',
    hasGsc: totalClicks > 0 || totalImpressions > 0,
    topUrls: ranked.slice(0, 5).map((e) => ({
      url: e.url,
      clicks: e.clicks,
      impressions: e.impressions,
      kind: (e as DiscoveredEntry & { kind?: string }).kind,
    })),
  };
}
