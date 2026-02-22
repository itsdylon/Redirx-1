import { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardAction, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';
import { Mail, RefreshCw, Loader2, Send } from 'lucide-react';
import {
  listEmailTemplates,
  sendTestEmail,
  getEmailLog,
  type EmailTemplate,
  type EmailLogEntry,
} from '../api/email';
import { toast } from 'sonner';

const STATUS_COLORS: Record<string, string> = {
  sent: 'bg-green-500/10 text-green-600 border-green-500/30',
  failed: 'bg-red-500/10 text-red-600 border-red-500/30',
  skipped: 'bg-yellow-500/10 text-yellow-600 border-yellow-500/30',
};

export function AdminEmailTesting() {
  const [templates, setTemplates] = useState<EmailTemplate[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [toEmail, setToEmail] = useState('');
  const [sending, setSending] = useState(false);
  const [logEntries, setLogEntries] = useState<EmailLogEntry[]>([]);
  const [loadingLog, setLoadingLog] = useState(false);

  useEffect(() => {
    loadTemplates();
    loadLog();
  }, []);

  async function loadTemplates() {
    try {
      const data = await listEmailTemplates();
      setTemplates(data);
    } catch {
      // Silently fail — admin page already checks access
    }
  }

  async function loadLog() {
    setLoadingLog(true);
    try {
      const data = await getEmailLog({ limit: 10 });
      setLogEntries(data);
    } catch {
      // Silently fail
    } finally {
      setLoadingLog(false);
    }
  }

  async function handleSend() {
    if (!selectedId) return;
    setSending(true);
    try {
      const result = await sendTestEmail({
        template_id: selectedId,
        to_email: toEmail || undefined,
      });
      if (result.success) {
        toast.success('Test email sent');
        await loadLog();
      } else {
        toast.error(result.error || 'Email was not sent');
      }
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setSending(false);
    }
  }

  // Group templates by category for the dropdown
  const grouped = templates.reduce<Record<string, EmailTemplate[]>>((acc, t) => {
    (acc[t.category] ||= []).push(t);
    return acc;
  }, {});

  const selectedTemplate = templates.find((t) => t.id === selectedId);

  const categoryLabels: Record<string, string> = {
    transactional: 'Transactional',
    onboarding: 'Onboarding',
  };

  return (
    <Card className="mt-6">
      <CardHeader className="border-b border-border">
        <CardTitle className="flex items-center gap-2">
          <Mail className="h-5 w-5" />
          Email Testing
        </CardTitle>
        <CardAction>
          <Button variant="outline" size="sm" onClick={loadLog}>
            <RefreshCw className="h-4 w-4" />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="pt-6">
        {/* Send test form */}
        <div className="flex flex-col sm:flex-row items-start sm:items-end gap-3 mb-2">
          <div className="w-full sm:w-[240px]">
            <label className="block text-sm font-medium text-foreground mb-1">
              Template
            </label>
            <Select value={selectedId} onValueChange={setSelectedId}>
              <SelectTrigger>
                <SelectValue placeholder="Select template" />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(grouped).map(([category, items]) => (
                  <SelectGroup key={category}>
                    <SelectLabel>{categoryLabels[category] || category}</SelectLabel>
                    {items.map((t) => (
                      <SelectItem key={t.id} value={t.id}>
                        {t.name}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="w-full sm:w-[260px]">
            <label className="block text-sm font-medium text-foreground mb-1">
              Recipient <span className="text-muted-foreground text-xs">(optional)</span>
            </label>
            <Input
              type="email"
              value={toEmail}
              onChange={(e) => setToEmail(e.target.value)}
              placeholder="defaults to your email"
            />
          </div>
          <Button onClick={handleSend} disabled={sending || !selectedId}>
            {sending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Sending...
              </>
            ) : (
              <>
                <Send className="h-4 w-4 mr-2" />
                Send Test
              </>
            )}
          </Button>
        </div>

        {selectedTemplate && (
          <p className="text-xs text-muted-foreground mb-4">
            {selectedTemplate.description}
          </p>
        )}

        {/* Recent sends */}
        <div className="mt-4">
          <h4 className="text-sm font-medium text-foreground mb-2">Recent Sends</h4>
          {loadingLog ? (
            <div className="py-6 text-center">
              <Loader2 className="h-5 w-5 animate-spin mx-auto text-muted-foreground" />
            </div>
          ) : logEntries.length === 0 ? (
            <div className="py-6 text-center text-muted-foreground text-sm">
              No email log entries yet.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Type</TableHead>
                  <TableHead>Subject</TableHead>
                  <TableHead>Recipient</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Sent</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logEntries.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell>
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded">
                        {entry.email_type}
                      </code>
                    </TableCell>
                    <TableCell className="text-sm max-w-[240px] truncate">
                      {entry.subject}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {entry.to_email}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={STATUS_COLORS[entry.status] || ''}
                      >
                        {entry.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      {new Date(entry.created_at).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
