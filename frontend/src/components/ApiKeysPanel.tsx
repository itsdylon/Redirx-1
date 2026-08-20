import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Copy, KeyRound, Loader2, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

import { Card } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';
import { queryKeys } from '../queries/queryKeys';
import { formatDate, formatDateTime } from '../utils/date';
import {
  createApiKey,
  isActive,
  listApiKeys,
  revokeApiKey,
  type ApiKey,
  type CreatedApiKey,
} from '../api/keys';

/**
 * The plaintext key, shown exactly once.
 *
 * Deliberately loud and deliberately not dismissible by accident: the server
 * stores only a hash, so if this is closed before the key is copied it is gone
 * and the only remedy is issuing another one. Says so plainly rather than
 * relying on the user knowing how API keys work.
 */
function NewKeyReveal({ created, onDone }: { created: CreatedApiKey; onDone: () => void }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(created.key);
      setCopied(true);
      toast.success('Key copied');
    } catch {
      // Clipboard is unavailable over plain http and in some embedded views.
      // The key is selectable on screen, so this is a downgrade, not a failure.
      toast.error('Could not copy automatically — select the key and copy it');
    }
  };

  return (
    <Card className="border-primary/40 bg-primary/5 p-4">
      <p className="text-sm font-medium text-foreground">
        Copy your key now — this is the only time it will be shown
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        We store a hash, not the key itself, so it cannot be shown again. If you
        lose it, delete it and create another.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground">
          {created.key}
        </code>
        <Button size="sm" onClick={copy}>
          {copied ? <Check className="mr-1.5 h-4 w-4" /> : <Copy className="mr-1.5 h-4 w-4" />}
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>

      <Button size="sm" variant="ghost" className="mt-3" onClick={onDone}>
        I've saved it
      </Button>
    </Card>
  );
}

function KeyRow({ apiKey, onRevoke }: { apiKey: ApiKey; onRevoke: (key: ApiKey) => void }) {
  const active = isActive(apiKey);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 last:border-b-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-foreground">{apiKey.name}</span>
          {!active && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
              Revoked
            </span>
          )}
        </div>
        <code className="mt-0.5 block font-mono text-xs text-muted-foreground">
          {apiKey.key_prefix}…
        </code>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Created {formatDate(apiKey.created_at)}
          {' · '}
          {apiKey.last_used_at
            ? `last used ${formatDateTime(apiKey.last_used_at)}`
            : 'never used'}
        </p>
      </div>

      {active && (
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-destructive"
          onClick={() => onRevoke(apiKey)}
        >
          <Trash2 className="mr-1.5 h-4 w-4" />
          Delete
        </Button>
      )}
    </div>
  );
}

/**
 * Issue and manage API keys.
 *
 * Self-contained so it can sit in Settings for agency accounts and on its own
 * route for everyone else — a free account can drive Quick Match over the API,
 * so key management cannot live only behind the plans that can reach Settings.
 */
export function ApiKeysPanel() {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const [pendingRevoke, setPendingRevoke] = useState<ApiKey | null>(null);

  const keysQuery = useQuery({
    queryKey: queryKeys.apiKeys.all,
    queryFn: listApiKeys,
  });

  const createMutation = useMutation({
    mutationFn: () => createApiKey(name.trim() || 'API key'),
    onSuccess: (key) => {
      setCreated(key);
      setName('');
      queryClient.invalidateQueries({ queryKey: queryKeys.apiKeys.all });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const revokeMutation = useMutation({
    mutationFn: (keyId: string) => revokeApiKey(keyId),
    onSuccess: () => {
      toast.success('Key deleted');
      queryClient.invalidateQueries({ queryKey: queryKeys.apiKeys.all });
    },
    onError: (error: Error) => toast.error(error.message),
    onSettled: () => setPendingRevoke(null),
  });

  const keys = keysQuery.data ?? [];
  const activeKeys = keys.filter(isActive);

  return (
    <div className="space-y-4">
      <Card className="p-6 space-y-4">
        <div className="flex items-start gap-3">
          <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
          <div>
            <h2 className="text-base font-medium text-foreground">API keys</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Let an agent run a migration without a browser. Send the key as{' '}
              <code className="font-mono text-xs">Authorization: Bearer rdx_…</code>{' '}
              to <code className="font-mono text-xs">/api/v1</code>.
            </p>
          </div>
        </div>

        {created && (
          <NewKeyReveal created={created} onDone={() => setCreated(null)} />
        )}

        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[200px] flex-1 space-y-2">
            <Label htmlFor="key-name">Name</Label>
            <Input
              id="key-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Claude Code"
              maxLength={100}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !createMutation.isPending) {
                  createMutation.mutate();
                }
              }}
            />
          </div>
          <Button onClick={() => createMutation.mutate()} disabled={createMutation.isPending}>
            {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Create key
          </Button>
        </div>
      </Card>

      <Card className="p-6">
        {keysQuery.isLoading ? (
          <div className="flex justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : keysQuery.error ? (
          <p className="py-4 text-sm text-muted-foreground">
            {keysQuery.error instanceof Error
              ? keysQuery.error.message
              : 'Could not load your keys.'}
          </p>
        ) : keys.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">
            No keys yet. Create one above to start driving Redirx from an agent.
          </p>
        ) : (
          <>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {activeKeys.length} active
            </p>
            {keys.map((key) => (
              <KeyRow key={key.id} apiKey={key} onRevoke={setPendingRevoke} />
            ))}
          </>
        )}
      </Card>

      <AlertDialog
        open={pendingRevoke !== null}
        onOpenChange={(open) => !open && setPendingRevoke(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{pendingRevoke?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Anything using this key stops working immediately. This cannot be
              undone — you would need to create a new key and update whatever
              was using it.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => pendingRevoke && revokeMutation.mutate(pendingRevoke.id)}
            >
              Delete key
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
