/**
 * Centralized error handling utility for API calls
 * Provides user-friendly error messages with actionable guidance
 */

export interface UserFriendlyError {
  message: string;
  type: string;
  retryable: boolean;
  retryAfter?: number;
  originalError?: any;
}

/**
 * Categorizes and handles API errors, converting them to user-friendly messages
 *
 * @param error - The error object from fetch or other source
 * @param response - Optional Response object for parsing status codes
 * @returns UserFriendlyError with categorized information
 */
export async function handleApiError(
  error: any,
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
  if (error.name === 'TypeError' || error.name === 'NetworkError') {
    return {
      message: 'Connection lost. Check your internet and try again.',
      type: 'network_error',
      retryable: true,
      originalError: error,
    };
  }

  // Timeout errors
  if (error.name === 'AbortError' || error.message?.includes('timeout')) {
    return {
      message: 'Request timed out. The server might be busy. Please try again.',
      type: 'timeout_error',
      retryable: true,
      originalError: error,
    };
  }

  // If we have a response object, handle HTTP status codes
  if (response) {
    // Try to parse error message from response
    let errorData: any = {};
    try {
      errorData = await response.json();
    } catch {
      // Response body might not be JSON
      errorData = {};
    }

    // 429 Rate Limiting
    if (response.status === 429) {
      const retryAfter = response.headers.get('Retry-After');
      const retrySeconds = retryAfter ? parseInt(retryAfter, 10) : 60;

      return {
        message: `Too many requests. Please wait ${retrySeconds} seconds before trying again.`,
        type: 'rate_limit',
        retryable: true,
        retryAfter: retrySeconds,
        originalError: errorData,
      };
    }

    // 401 Unauthorized
    if (response.status === 401) {
      return {
        message: errorData.error || errorData.message || 'Unauthorized. Please log in again.',
        type: 'unauthorized',
        retryable: false,
        originalError: errorData,
      };
    }

    // 403 Forbidden
    if (response.status === 403) {
      return {
        message: errorData.error || errorData.message || 'You do not have permission to perform this action.',
        type: 'forbidden',
        retryable: false,
        originalError: errorData,
      };
    }

    // 404 Not Found
    if (response.status === 404) {
      return {
        message: errorData.error || errorData.message || 'The requested resource was not found.',
        type: 'not_found',
        retryable: false,
        originalError: errorData,
      };
    }

    // 400-499 Client Errors (other than above)
    if (response.status >= 400 && response.status < 500) {
      return {
        message: errorData.error || errorData.message || `Request error: ${response.status}`,
        type: 'client_error',
        retryable: false,
        originalError: errorData,
      };
    }

    // 500-599 Server Errors
    if (response.status >= 500) {
      return {
        message: 'Server error. Please try again in a few minutes.',
        type: 'server_error',
        retryable: true,
        originalError: errorData,
      };
    }
  }

  // If error has a message property, use it
  if (error?.message) {
    return {
      message: error.message,
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
 * Simplified error handler for cases where response is not available
 * Synchronous version that handles basic error categorization
 */
export function handleSimpleError(error: any): UserFriendlyError {
  // Network errors
  if (error instanceof TypeError || error.name === 'TypeError' || error.name === 'NetworkError') {
    return {
      message: 'Connection lost. Check your internet and try again.',
      type: 'network_error',
      retryable: true,
      originalError: error,
    };
  }

  // Timeout errors
  if (error.name === 'AbortError' || error.message?.includes('timeout')) {
    return {
      message: 'Request timed out. The server might be busy. Please try again.',
      type: 'timeout_error',
      retryable: true,
      originalError: error,
    };
  }

  // Use error message if available
  if (error?.message) {
    return {
      message: error.message,
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
