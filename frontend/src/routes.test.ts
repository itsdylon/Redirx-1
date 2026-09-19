import { describe, expect, it } from 'vitest';
import { ROUTES, getPivotEntryRedirect } from './routes';

describe('MCP pivot entry retirement', () => {
  it.each(['/quick-match', '/upload', '/dashboard'])('sends signed-out %s to companion login', (path) => {
    expect(getPivotEntryRedirect(path, false, true)).toBe(`${ROUTES.login}?redirect=%2Fcompanion`);
  });

  it.each(['/quick-match', '/upload', '/dashboard'])('sends signed-in %s to companion', (path) => {
    expect(getPivotEntryRedirect(path, true, true)).toBe(ROUTES.companion);
  });

  it.each(['/quick-match', '/upload', '/dashboard'])('leaves %s unchanged when the flag is off', (path) => {
    expect(getPivotEntryRedirect(path, false, false)).toBeNull();
    expect(getPivotEntryRedirect(path, true, false)).toBeNull();
  });

  it('does not classify retained review, history, billing, account, or settings paths as retired entries', () => {
    for (const path of ['/review/session-1', '/projects', '/billing/subscriptions/return', '/account', '/settings']) {
      expect(getPivotEntryRedirect(path, false, true)).toBeNull();
      expect(getPivotEntryRedirect(path, true, true)).toBeNull();
    }
  });
});
