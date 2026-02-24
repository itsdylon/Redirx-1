/**
 * Centralized error handling utility for API calls.
 * Keeps legacy user-friendly helpers while adding structured API error support.
 */

export interface ApiErrorPayload {
  success?: boolean;
  error?: string;
  code?: string;
  user_message?: string;
  retryable?: boolean;
  next_action?: string;
  retry_after_seconds?: number;
  message?: string;
  [key: string]: unknown;
}

export class ApiError extends Error {
  status?: number;
  code?: string;
  user_message?: string;
  retryable?: boolean;
  next_action?: string;
  retry_after_seconds?: number;
  payload?: ApiErrorPayload | null;
  raw_body?: string;

  constructor(
    message: string,
    options?: {
      status?: number;
      code?: string;
      user_message?: string;
      retryable?: boolean;
      next_action?: string;
      retry_after_seconds?: number;
      payload?: ApiErrorPayload | null;
      raw_body?: string;
    }
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = options?.status;
    this.code = options?.code;
    this.user_message = options?.user_message;
    this.retryable = options?.retryable;
    this.next_action = options?.next_action;
    this.retry_after_seconds = options?.retry_after_seconds;
    this.payload = options?.payload;
    this.raw_body = options?.raw_body;
  }
}

export interface UserFriendlyError {
  message: string;
  type: string;
  retryable: boolean;
  retryAfter?: number;
  originalError?: unknown;
}

function statusFallbackMessage(status: number, fallbackMessage?: string): string {
  if (fallbackMessage) {
    return fallbackMessage;
  }

  if (status === 400) {
    return 'Please check your request and try again.';
  }
  if (status === 401) {
    return 'Unauthorized. Please log in again.';
  }
  if (status === 403) {
    return 'You do not have permission to perform this action.';
  }
  if (status === 404) {
    return 'The requested resource was not found.';
  }
  if (status === 429) {
    return 'Too many requests. Please wait and try again.';
  }
  if (status >= 500) {
    return 'Server error. Please try again in a few minutes.';
  }

  return `Request failed (${status}).`;
}

function parseRetryAfterSeconds(
  payload: ApiErrorPayload | null,
  response: Response
): number | undefined {
  if (typeof payload?.retry_after_seconds === 'number' && Number.isFinite(payload.retry_after_seconds)) {
    return payload.retry_after_seconds;
  }

  const headerValue = response.headers?.get?.('Retry-After');
  if (!headerValue) {
    return undefined;
  }

  const parsed = Number.parseInt(headerValue, 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return undefined;
  }

  return parsed;
}

function safeBodyMessage(rawBody: string): string | undefined {
  const trimmed = rawBody.trim();
  if (!trimmed || trimmed.length > 200) {
    return undefined;
  }

  // Avoid leaking HTML/stack traces or serialized internals to users.
  const looksTechnical =
    trimmed.includes('<') ||
    trimmed.includes('{') ||
    trimmed.toLowerCase().includes('traceback') ||
    trimmed.toLowerCase().includes('exception');

  if (looksTechnical) {
    return undefined;
  }

  return trimmed;
}

function parseJsonSafely(rawBody: string): ApiErrorPayload | null {
  if (!rawBody.trim()) {
    return null;
  }

  try {
    const parsed = JSON.parse(rawBody);
    if (parsed && typeof parsed === 'object') {
      return parsed as ApiErrorPayload;
    }
    return null;
  } catch {
    return null;
  }
}

export async function parseApiErrorResponse(response: Response): Promise<{
  payload: ApiErrorPayload | null;
  rawBody: string;
}> {
  let rawBody = '';
  const responseLike = response as unknown as {
    text?: () => Promise<string>;
    json?: () => Promise<unknown>;
  };

  if (typeof responseLike.text === 'function') {
    try {
      rawBody = await responseLike.text();
    } catch {
      rawBody = '';
    }
  } else if (typeof responseLike.json === 'function') {
    try {
      const jsonValue = await responseLike.json();
      rawBody = JSON.stringify(jsonValue ?? {});
    } catch {
      rawBody = '';
    }
  }

  const payload = parseJsonSafely(rawBody);
  return { payload, rawBody };
}

export async function toApiError(
  response: Response,
  fallbackMessage?: string
): Promise<ApiError> {
  const { payload, rawBody } = await parseApiErrorResponse(response);
  const retryAfterSeconds = parseRetryAfterSeconds(payload, response);
  const statusFallback = statusFallbackMessage(response.status, fallbackMessage);
  const message =
    payload?.user_message ||
    payload?.error ||
    payload?.message ||
    safeBodyMessage(rawBody) ||
    statusFallback;

  return new ApiError(message, {
    status: response.status,
    code: payload?.code,
    user_message: payload?.user_message,
    retryable: payload?.retryable ?? (response.status >= 500 || response.status === 429),
    next_action: payload?.next_action,
    retry_after_seconds: retryAfterSeconds,
    payload,
    raw_body: rawBody,
  });
}

export async function throwApiErrorFromResponse(
  response: Response,
  fallbackMessage?: string
): Promise<never> {
  throw await toApiError(response, fallbackMessage);
}

/**
 * Categorizes and handles API errors, converting them to user-friendly messages.
 *
 * @param error - The error object from fetch or other source
 * @param response - Optional Response object for parsing status codes
 * @returns UserFriendlyError with categorized information
 */
export async function handleApiError(
  error: unknown,
  response?: Response
): Promise<UserFriendlyError> {
  // Network errors (TypeError from fetch, AbortError, etc.)
  if (error instanceof TypeError && error.message.includes('fetch')) {
    return {
      message: 'Connection lost. Check your internet and try again.',
      type: 'network_error',
      retryable: true,
      originalError: error,
    };
  }

  // Generic network/connection errors
  if ((error as { name?: string } | undefined)?.name === 'TypeError' || (error as { name?: string } | undefined)?.name === 'NetworkError') {
    return {
      message: 'Connection lost. Check your internet and try again.',
      type: 'network_error',
      retryable: true,
      originalError: error,
    };
  }

  // Timeout errors
  if ((error as { name?: string; message?: string } | undefined)?.name === 'AbortError' || (error as { message?: string } | undefined)?.message?.includes('timeout')) {
    return {
      message: 'Request timed out. The server might be busy. Please try again.',
      type: 'timeout_error',
      retryable: true,
      originalError: error,
    };
  }

  // If we have a response object, handle HTTP status codes
  if (response) {
    const apiError = await toApiError(response);

    // 429 Rate Limiting
    if (response.status === 429) {
      return {
        message: apiError.message,
        type: 'rate_limit',
        retryable: true,
        retryAfter: apiError.retry_after_seconds ?? 60,
        originalError: apiError.payload ?? apiError.raw_body,
      };
    }

    // 401 Unauthorized
    if (response.status === 401) {
      return {
        message: apiError.message,
        type: 'unauthorized',
        retryable: false,
        originalError: apiError.payload ?? apiError.raw_body,
      };
    }

    // 403 Forbidden
    if (response.status === 403) {
      return {
        message: apiError.message,
        type: 'forbidden',
        retryable: false,
        originalError: apiError.payload ?? apiError.raw_body,
      };
    }

    // 404 Not Found
    if (response.status === 404) {
      return {
        message: apiError.message,
        type: 'not_found',
        retryable: false,
        originalError: apiError.payload ?? apiError.raw_body,
      };
    }

    // 400-499 Client Errors (other than above)
    if (response.status >= 400 && response.status < 500) {
      return {
        message: apiError.message,
        type: 'client_error',
        retryable: false,
        originalError: apiError.payload ?? apiError.raw_body,
      };
    }

    // 500-599 Server Errors
    if (response.status >= 500) {
      return {
        message: apiError.message,
        type: 'server_error',
        retryable: true,
        originalError: apiError.payload ?? apiError.raw_body,
      };
    }
  }

  // If error has a message property, use it
  if ((error as { message?: string } | undefined)?.message) {
    return {
      message: (error as { message: string }).message,
      type: 'unknown_error',
      retryable: false,
      originalError: error,
    };
  }

  // Fallback for completely unknown errors
  return {
    message: 'An unexpected error occurred. Please try again.',
    type: 'unknown_error',
    retryable: false,
    originalError: error,
  };
}

/**
 * Simplified error handler for cases where response is not available.
 * Synchronous version that handles basic error categorization.
 */
export function handleSimpleError(error: unknown): UserFriendlyError {
  // Network errors
  if (error instanceof TypeError || (error as { name?: string } | undefined)?.name === 'TypeError' || (error as { name?: string } | undefined)?.name === 'NetworkError') {
    return {
      message: 'Connection lost. Check your internet and try again.',
      type: 'network_error',
      retryable: true,
      originalError: error,
    };
  }

  // Timeout errors
  if ((error as { name?: string; message?: string } | undefined)?.name === 'AbortError' || (error as { message?: string } | undefined)?.message?.includes('timeout')) {
    return {
      message: 'Request timed out. The server might be busy. Please try again.',
      type: 'timeout_error',
      retryable: true,
      originalError: error,
    };
  }

  // Use error message if available
  if ((error as { message?: string } | undefined)?.message) {
    return {
      message: (error as { message: string }).message,
      type: 'unknown_error',
      retryable: false,
      originalError: error,
    };
  }

  // Fallback
  return {
    message: 'An unexpected error occurred. Please try again.',
    type: 'unknown_error',
    retryable: false,
    originalError: error,
  };
}
