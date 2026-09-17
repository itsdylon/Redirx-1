const AUTH_REDIRECT_KEY = 'auth_redirect';

export function sanitizeRedirectPath(path: string | null | undefined): string | null {
  if (!path) return null;
  // Auth redirects must stay on this SPA. Backslashes are rejected because
  // browsers normalize them as URL separators in several redirect contexts.
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) return null;
  let decodedPath = path;
  try {
    decodedPath = decodeURIComponent(path);
  } catch {
    return null;
  }
  if (decodedPath.includes('\\') || decodedPath.startsWith('//')) return null;
  if (/[\u0000-\u001f\u007f]/.test(path) || /[\u0000-\u001f\u007f]/.test(decodedPath)) return null;

  try {
    const target = new URL(path, window.location.origin);
    if (target.origin !== window.location.origin || !target.pathname.startsWith('/')) return null;
  } catch {
    return null;
  }

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
