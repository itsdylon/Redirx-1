import { API_BASE_URL, getAuthHeaders } from './config';

// ============================================================================
// Types
// ============================================================================

export interface EmailTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
}

export interface EmailLogEntry {
  id: string;
  user_id?: string;
  to_email: string;
  email_type: string;
  subject: string;
  status: string;
  error?: string;
  resend_message_id?: string;
  created_at: string;
}

// ============================================================================
// Admin: Email Templates & Testing
// ============================================================================

export async function listEmailTemplates(): Promise<EmailTemplate[]> {
  const res = await fetch(`${API_BASE_URL}/api/email/admin/templates`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to list templates');
  }
  const json = await res.json();
  return json.templates;
}

export async function sendTestEmail(data: {
  template_id: string;
  to_email?: string;
}): Promise<{ success: boolean; message_id?: string; error?: string }> {
  const res = await fetch(`${API_BASE_URL}/api/email/admin/test`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to send test email');
  }
  return res.json();
}

// ============================================================================
// User: Email Preferences
// ============================================================================

export interface EmailPreference {
  email_type: string;
  opted_out: boolean;
}

export async function getEmailPreferences(): Promise<EmailPreference[]> {
  const res = await fetch(`${API_BASE_URL}/api/email/preferences`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to fetch email preferences');
  }
  const json = await res.json();
  return json.preferences;
}

export async function updateEmailPreference(
  emailType: string,
  optedOut: boolean
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/email/preferences`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify({ email_type: emailType, opted_out: optedOut }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to update email preference');
  }
}

// ============================================================================
// Admin: Email Log
// ============================================================================

export async function getEmailLog(params?: {
  limit?: number;
  email_type?: string;
}): Promise<EmailLogEntry[]> {
  const searchParams = new URLSearchParams();
  if (params?.limit) searchParams.set('limit', String(params.limit));
  if (params?.email_type) searchParams.set('email_type', params.email_type);

  const qs = searchParams.toString();
  const url = `${API_BASE_URL}/api/email/admin/log${qs ? '?' + qs : ''}`;

  const res = await fetch(url, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error || 'Failed to fetch email log');
  }
  const json = await res.json();
  return json.entries;
}
