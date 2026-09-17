import { describe, expect, it, beforeEach } from 'vitest';
import { getAuthRedirect, setAuthRedirect } from './authRedirect';

describe('auth redirect safety', () => {
  beforeEach(() => localStorage.clear());

  it.each([
    ['//evil.example/path', null],
    ['/\\evil.example/path', null],
    ['/%5C%5Cevil.example/path', null],
    ['https://evil.example/path', null],
    ['/oauth/consent?authorization_id=request-1', '/oauth/consent?authorization_id=request-1'],
  ])('accepts only local redirect %s', (candidate, expected) => {
    expect(setAuthRedirect(candidate)).toBe(expected);
    expect(getAuthRedirect()).toBe(expected);
  });
});
