import type { NavigateFunction } from 'react-router-dom';
import { ApiError } from '../utils/errorHandler';

export function clearAuthTokens(): void {
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
}

export function isUnauthorizedError(error: unknown): boolean {
  if (error instanceof ApiError) {
    if (error.status === 401) return true;
    if (typeof error.code === 'string' && error.code.toLowerCase().includes('auth')) {
      return true;
    }
  }

  if (error instanceof Error) {
    const message = error.message.toLowerCase();
    return message.includes('unauthorized') || message.includes('log in again');
  }

  return false;
}

export function handleUnauthorizedAndRedirect(
  error: unknown,
  navigate: NavigateFunction,
): boolean {
  if (!isUnauthorizedError(error)) {
    return false;
  }

  clearAuthTokens();
  navigate('/login');
  return true;
}
