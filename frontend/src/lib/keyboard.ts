/**
 * Keyboard shortcuts utility functions
 */

/**
 * Detects if the user is on macOS
 */
export const isMac = (): boolean => {
  return typeof window !== 'undefined' && navigator.platform.toUpperCase().indexOf('MAC') >= 0;
};

/**
 * Gets the modifier key symbol based on platform
 * @param type - 'symbol' for ⌘/Ctrl, 'name' for 'Cmd'/'Ctrl'
 */
export const getModifierKey = (type: 'symbol' | 'name' = 'symbol'): string => {
  const mac = isMac();

  if (type === 'symbol') {
    return mac ? '⌘' : 'Ctrl';
  }

  return mac ? 'Cmd' : 'Ctrl';
};

/**
 * Formats a keyboard shortcut for display
 * @param key - The key (e.g., 'K', 'E', 'A')
 * @param modifier - Whether to include the modifier key (default: true)
 */
export const formatShortcut = (key: string, modifier: boolean = true): string => {
  if (!modifier) {
    return key;
  }

  return `${getModifierKey('symbol')}+${key}`;
};
