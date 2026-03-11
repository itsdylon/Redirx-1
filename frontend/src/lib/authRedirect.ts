const AUTH_REDIRECT_KEY = 'auth_redirect';

function sanitizeRedirectPath(path: string | null | undefined): string | null {
  if (!path) return null;
  if (!path.startsWith('/') || path.startsWith('//')) return null;
  return path;
}

export function setAuthRedirect(path: string | null | undefined): string | null {
  const safePath = sanitizeRedirectPath(path);
  if (!safePath) return null;
  localStorage.setItem(AUTH_REDIRECT_KEY, safePath);
  return safePath;
}

export function getAuthRedirect(): string | null {
  return sanitizeRedirectPath(localStorage.getItem(AUTH_REDIRECT_KEY));
}

export function consumeAuthRedirect(): string | null {
  const redirect = getAuthRedirect();
  if (redirect) {
    localStorage.removeItem(AUTH_REDIRECT_KEY);
  }
  return redirect;
}
