/**
 * Utility functions for date handling.
 * Supabase returns timestamps in ISO format (UTC), but without explicit 'Z' suffix
 * in some cases, which causes JavaScript to interpret them as local time.
 */

/**
 * Parse a database timestamp and format it as a local date string.
 * Handles the case where Supabase returns timestamps without timezone indicator.
 *
 * @param timestamp - ISO timestamp string from database
 * @returns Formatted local date string
 */
export function formatDate(timestamp: string | null | undefined): string {
  if (!timestamp) return '-';

  // If timestamp doesn't have timezone info, treat it as UTC
  let normalizedTimestamp = timestamp;
  if (!timestamp.endsWith('Z') && !timestamp.includes('+') && !timestamp.includes('-', 10)) {
    normalizedTimestamp = timestamp + 'Z';
  }

  const date = new Date(normalizedTimestamp);

  // Check for invalid date
  if (isNaN(date.getTime())) {
    return '-';
  }

  return date.toLocaleDateString();
}

/**
 * Parse a database timestamp and format it as a local date/time string.
 *
 * @param timestamp - ISO timestamp string from database
 * @returns Formatted local date and time string
 */
export function formatDateTime(timestamp: string | null | undefined): string {
  if (!timestamp) return '-';

  // If timestamp doesn't have timezone info, treat it as UTC
  let normalizedTimestamp = timestamp;
  if (!timestamp.endsWith('Z') && !timestamp.includes('+') && !timestamp.includes('-', 10)) {
    normalizedTimestamp = timestamp + 'Z';
  }

  const date = new Date(normalizedTimestamp);

  // Check for invalid date
  if (isNaN(date.getTime())) {
    return '-';
  }

  return date.toLocaleString();
}
