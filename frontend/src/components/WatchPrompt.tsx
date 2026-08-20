import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Activity, ArrowRight, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Card } from './ui/card';
import { Button } from './ui/button';
import { queryKeys } from '../queries/queryKeys';
import { createWatch, listWatches } from '../api/watch';

/**
 * Offer post-cutover monitoring from the review page.
 *
 * Placed here rather than in the export modal because the offer only makes
 * sense once, and a modal the user dismisses to download their file is the
 * wrong place to put something they should still be able to find tomorrow.
 *
 * Renders as a link to the existing watch when there already is one, so this
 * doubles as the way back into monitoring for a returning user.
 */
export function WatchPrompt({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const watchesQuery = useQuery({
    queryKey: queryKeys.watches.all,
    queryFn: listWatches,
  });

  const existing = watchesQuery.data?.find((w) => w.session_id === sessionId);

  const createMutation = useMutation({
    mutationFn: () => createWatch({ sessionId }),
    onSuccess: (watch) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.watches.all });
      toast.success('Monitoring started — we’ll email you if anything breaks.');
      navigate(`/watch/${watch.id}`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  if (watchesQuery.isLoading) return null;

  return (
    <Card className="mt-6 p-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-start gap-3">
          <Activity className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
          <div>
            <p className="text-sm font-medium text-foreground">
              {existing ? 'Monitoring is on' : 'Watch these redirects after you deploy'}
            </p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {existing
                ? 'We check the live site and email you when something breaks.'
                : 'We check the live site on a schedule and email you if a redirect 404s, lands on the wrong page, or never shipped.'}
            </p>
          </div>
        </div>

        {existing ? (
          <Button variant="outline" size="sm" onClick={() => navigate(`/watch/${existing.id}`)}>
            View
            <ArrowRight className="ml-1.5 h-4 w-4" />
          </Button>
        ) : (
          <Button
            size="sm"
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
          >
            {createMutation.isPending && (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            )}
            Start monitoring
          </Button>
        )}
      </div>
    </Card>
  );
}
