/**
 * URL Validation Utilities
 *
 * Provides validation functions for URLs used in redirect mappings.
 * Accepts both relative paths and absolute URLs.
 */

export interface ValidationResult {
  valid: boolean;
  error: string | null;
}

/**
 * Validates an email address format.
 *
 * Checks for:
 * - Valid email format with @ symbol and domain
 * - No spaces in email address
 * - Valid characters only
 *
 * @param email - The email string to validate
 * @returns ValidationResult with valid flag and optional error message
 */
export function validateEmail(email: string): ValidationResult {
  // Check for empty or whitespace-only input
  if (!email || email.trim().length === 0) {
    return {
      valid: false,
      error: 'Email cannot be empty',
    };
  }

  const trimmedEmail = email.trim();

  // Check for spaces
  if (trimmedEmail.includes(' ')) {
    return {
      valid: false,
      error: 'Email cannot contain spaces',
    };
  }

  // Check for @ symbol
  if (!trimmedEmail.includes('@')) {
    return {
      valid: false,
      error: 'Email must include @ symbol',
    };
  }

  // Basic email regex validation
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  if (!emailRegex.test(trimmedEmail)) {
    // Provide more specific error messages based on common mistakes
    const parts = trimmedEmail.split('@');

    if (parts.length > 2) {
      return {
        valid: false,
        error: 'Email contains multiple @ symbols',
      };
    }

    if (parts.length === 2 && !parts[1].includes('.')) {
      return {
        valid: false,
        error: 'Email must include a domain (e.g., example.com)',
      };
    }

    // Generic invalid format message
    return {
      valid: false,
      error: 'Invalid email format',
    };
  }

  return { valid: true, error: null };
}

/**
 * Validates a URL for use in redirect mappings.
 *
 * Accepts:
 * - Relative paths starting with / (e.g., /about, /products/item)
 * - Full URLs with http:// or https:// protocol
 *
 * @param url - The URL string to validate
 * @returns ValidationResult with valid flag and optional error message
 */
export function validateUrl(url: string): ValidationResult {
  // Check for empty or whitespace-only input
  if (!url || url.trim().length === 0) {
    return {
      valid: false,
      error: 'URL cannot be empty',
    };
  }

  const trimmedUrl = url.trim();

  // Check for leading/trailing whitespace (indicates user error)
  if (trimmedUrl !== url) {
    return {
      valid: false,
      error: 'URL cannot have leading or trailing spaces',
    };
  }

  // Case 1: Relative path (starts with /)
  if (trimmedUrl.startsWith('/')) {
    // Check for invalid characters in path
    // Allow: alphanumeric, /, -, _, ., ~, %, ?, &, =, #
    const validPathRegex = /^\/[\w\-._~%/?&=#+]*$/;

    if (!validPathRegex.test(trimmedUrl)) {
      return {
        valid: false,
        error: 'Relative path contains invalid characters',
      };
    }

    // Check for double slashes (except after protocol which doesn't apply here)
    if (trimmedUrl.includes('//')) {
      return {
        valid: false,
        error: 'Path cannot contain consecutive slashes (//)',
      };
    }

    // Check for spaces (should be encoded as %20)
    if (trimmedUrl.includes(' ')) {
      return {
        valid: false,
        error: 'Path cannot contain spaces (use %20 for encoded spaces)',
      };
    }

    return { valid: true, error: null };
  }

  // Case 2: Absolute URL (must start with http:// or https://)
  if (trimmedUrl.startsWith('http://') || trimmedUrl.startsWith('https://')) {
    try {
      const urlObj = new URL(trimmedUrl);

      // Check for spaces in URL
      if (trimmedUrl.includes(' ')) {
        return {
          valid: false,
          error: 'URL cannot contain spaces',
        };
      }

      // Check that hostname is present
      if (!urlObj.hostname || urlObj.hostname.length === 0) {
        return {
          valid: false,
          error: 'URL must include a valid hostname',
        };
      }

      // Check for valid hostname format (basic check)
      const hostnameRegex = /^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$/;
      if (!hostnameRegex.test(urlObj.hostname)) {
        return {
          valid: false,
          error: 'URL contains an invalid hostname',
        };
      }

      return { valid: true, error: null };
    } catch (e) {
      return {
        valid: false,
        error: 'Invalid URL format',
      };
    }
  }

  // Case 3: Neither relative path nor absolute URL
  return {
    valid: false,
    error: 'URL must start with / (relative path) or http:// / https:// (absolute URL)',
  };
}

/**
 * Debounce utility for delaying validation until user stops typing
 *
 * @param func - The function to debounce
 * @param wait - The delay in milliseconds
 * @returns Debounced function
 */
export function debounce<T extends (...args: any[]) => any>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: NodeJS.Timeout | null = null;

  return function executedFunction(...args: Parameters<T>) {
    const later = () => {
      timeout = null;
      func(...args);
    };

    if (timeout) {
      clearTimeout(timeout);
    }
    timeout = setTimeout(later, wait);
  };
}

/**
 * CSV file validation result
 */
export interface CSVValidationResult {
  valid: boolean;
  warnings: string[];
  errors: string[];
  rowCount: number;
}

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB
const WARN_FILE_SIZE = 5 * 1024 * 1024; // 5MB
const WARN_ROW_COUNT = 5000;

/**
 * Validates a CSV file for size, structure, and content
 *
 * @param file - The File object to validate
 * @returns Promise<CSVValidationResult> - Validation result with errors and warnings
 */
export async function validateCSV(file: File): Promise<CSVValidationResult> {
  const result: CSVValidationResult = {
    valid: true,
    warnings: [],
    errors: [],
    rowCount: 0,
  };

  // Check file extension
  const fileName = file.name.toLowerCase();
  if (!fileName.endsWith('.csv') && !fileName.endsWith('.txt')) {
    result.valid = false;
    result.errors.push('File must have a .csv or .txt extension');
    return result;
  }

  // Check file size - reject if > 10MB
  if (file.size > MAX_FILE_SIZE) {
    result.valid = false;
    result.errors.push(`File size (${formatFileSize(file.size)}) exceeds maximum allowed size of ${formatFileSize(MAX_FILE_SIZE)}`);
    return result;
  }

  // Warn if file size > 5MB
  if (file.size > WARN_FILE_SIZE) {
    result.warnings.push(`Large file detected (${formatFileSize(file.size)}). Processing may take longer.`);
  }

  // Check if file is empty
  if (file.size === 0) {
    result.valid = false;
    result.errors.push('File is empty');
    return result;
  }

  // Read and validate file content
  try {
    const text = await readFileAsText(file);

    // Check if file has any content
    if (text.trim().length === 0) {
      result.valid = false;
      result.errors.push('File contains no data');
      return result;
    }

    // Parse CSV structure
    const lines = text.split('\n').filter(line => line.trim());

    if (lines.length === 0) {
      result.valid = false;
      result.errors.push('File contains no valid rows');
      return result;
    }

    // Row count is total non-empty lines (no header assumption)
    result.rowCount = lines.length;

    // Warn if only 1 URL
    if (lines.length === 1) {
      result.warnings.push('File contains only 1 URL');
    }

    // Warn if row count is very large
    if (result.rowCount > WARN_ROW_COUNT) {
      result.warnings.push(`Large dataset detected (${result.rowCount.toLocaleString()} rows). Processing may take several minutes.`);
    }

    // Check for inconsistent column counts (only for multi-column CSVs)
    const firstLine = lines[0];
    if (firstLine.includes(',')) {
      const columnCounts = lines.slice(0, Math.min(10, lines.length)).map(line => {
        return line.split(',').length;
      });

      const firstColumnCount = columnCounts[0];
      const hasInconsistentColumns = columnCounts.some(count => count !== firstColumnCount);

      if (hasInconsistentColumns) {
        result.warnings.push('File may have inconsistent column counts across rows. This could indicate formatting issues.');
      }
    }

    // Validate that content looks like URLs
    // For multi-column CSVs, check the first column; for single-column, check the whole line
    const sampleSize = Math.min(20, lines.length);
    let nonUrlCount = 0;
    for (let i = 0; i < sampleSize; i++) {
      const value = lines[i].includes(',') ? lines[i].split(',')[0].trim() : lines[i].trim();
      // Skip if it looks like a header (common header names)
      if (i === 0 && /^(url|address|link|page|path|location|source|destination)s?$/i.test(value)) {
        continue;
      }
      if (!looksLikeUrl(value)) {
        nonUrlCount++;
      }
    }

    if (nonUrlCount === sampleSize) {
      result.valid = false;
      result.errors.push('File does not appear to contain URLs. Each row should contain a URL (e.g., https://example.com/page or /page).');
      return result;
    } else if (nonUrlCount > 0) {
      result.warnings.push(`${nonUrlCount} of the first ${sampleSize} rows don't appear to be valid URLs.`);
    }

    // Check for common encoding issues (non-ASCII characters that might indicate wrong encoding)
    const hasNullBytes = text.includes('\u0000');
    if (hasNullBytes) {
      result.valid = false;
      result.errors.push('File contains invalid characters. Please ensure the file is saved as UTF-8.');
      return result;
    }

  } catch (error) {
    result.valid = false;
    if (error instanceof Error) {
      result.errors.push(`Failed to read file: ${error.message}`);
    } else {
      result.errors.push('Failed to read file due to an unknown error');
    }
    return result;
  }

  return result;
}

/**
 * Reads a file as text using FileReader
 */
function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onload = (e) => {
      const result = e.target?.result;
      if (typeof result === 'string') {
        resolve(result);
      } else {
        reject(new Error('Failed to read file as text'));
      }
    };

    reader.onerror = () => {
      reject(new Error('File reading failed'));
    };

    reader.readAsText(file, 'UTF-8');
  });
}

/**
 * Checks if a string looks like a URL (absolute or relative path)
 */
function looksLikeUrl(value: string): boolean {
  if (!value) return false;
  return /^https?:\/\//i.test(value) || value.startsWith('/');
}

/**
 * Formats file size in human-readable format
 */
function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 Bytes';

  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

/**
 * Password strength result
 */
export interface PasswordStrength {
  strength: 'weak' | 'fair' | 'good' | 'strong';
  score: number;
  feedback: string[];
  criteria: {
    minLength: boolean;
    hasUppercase: boolean;
    hasLowercase: boolean;
    hasNumber: boolean;
    hasSpecial: boolean;
  };
}

/**
 * Calculates password strength based on multiple criteria
 *
 * @param password - The password string to evaluate
 * @returns PasswordStrength with strength level, score, feedback, and criteria status
 */
export function calculatePasswordStrength(password: string): PasswordStrength {
  const criteria = {
    minLength: password.length >= 8,
    hasUppercase: /[A-Z]/.test(password),
    hasLowercase: /[a-z]/.test(password),
    hasNumber: /[0-9]/.test(password),
    hasSpecial: /[^A-Za-z0-9]/.test(password),
  };

  // Calculate score (0-5, with minLength being required)
  const score = Object.values(criteria).filter(Boolean).length;

  // Determine strength level
  let strength: 'weak' | 'fair' | 'good' | 'strong';
  if (!criteria.minLength || score <= 2) {
    strength = 'weak';
  } else if (score === 3) {
    strength = 'fair';
  } else if (score === 4) {
    strength = 'good';
  } else {
    strength = 'strong';
  }

  // Generate feedback for missing criteria
  const feedback: string[] = [];
  if (!criteria.minLength) {
    feedback.push('Use at least 8 characters');
  }
  if (!criteria.hasUppercase) {
    feedback.push('Add an uppercase letter');
  }
  if (!criteria.hasLowercase) {
    feedback.push('Add a lowercase letter');
  }
  if (!criteria.hasNumber) {
    feedback.push('Add a number');
  }
  if (!criteria.hasSpecial) {
    feedback.push('Add a special character');
  }

  return {
    strength,
    score,
    feedback,
    criteria,
  };
}
