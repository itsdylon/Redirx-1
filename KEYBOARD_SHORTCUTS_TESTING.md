# Keyboard Shortcuts Testing Guide

This document provides a comprehensive checklist for testing the keyboard shortcuts implemented in Issue #17.

## Prerequisites
1. Start the dev server: `python dev.py`
2. Navigate to a review page with results (create a migration session if needed)
3. Open browser console to watch for errors

## Test Checklist

### 1. Focus Search Input (Ctrl/Cmd+K)

**Test Steps:**
- [ ] Press `Ctrl+K` (Windows/Linux) or `Cmd+K` (Mac)
- [ ] Verify search input gains focus
- [ ] Verify browser's default "search" behavior is prevented
- [ ] Type some text to confirm focus is correct
- [ ] Press `Ctrl+K` again while already focused - should keep focus

**Expected:** Search input receives focus, cursor blinks in input field

---

### 2. Open Export Modal (Ctrl/Cmd+E)

**Test Steps:**
- [ ] Press `Ctrl+E` (Windows/Linux) or `Cmd+E` (Mac)
- [ ] Verify Export modal opens
- [ ] Close modal with X button
- [ ] Press `Ctrl+E` again
- [ ] Verify modal reopens
- [ ] Verify tooltip on Export button shows correct shortcut

**Expected:** Export modal opens/closes correctly, no browser "Save Page" dialog

---

### 3. Close Modals (Escape)

**Test Steps:**
- [ ] Open Export modal (`Ctrl+E`)
- [ ] Press `Escape`
- [ ] Verify Export modal closes
- [ ] Click keyboard shortcuts button (bottom right)
- [ ] Press `Escape`
- [ ] Verify keyboard shortcuts dialog closes
- [ ] Click "Edit" on any redirect row
- [ ] Press `Escape`
- [ ] Verify edit dialog closes

**Expected:** Escape closes whichever modal is currently open (priority: export > shortcuts > edit)

---

### 4. Select All Visible Redirects (Ctrl/Cmd+A)

**Test Steps:**
- [ ] Press `Ctrl+A` (Windows/Linux) or `Cmd+A` (Mac)
- [ ] Verify all redirects on current page are selected
- [ ] Verify checkboxes are checked
- [ ] Verify selection count shows at bottom
- [ ] Verify toast notification appears
- [ ] Verify browser text selection is prevented
- [ ] Change to page 2 (if available)
- [ ] Press `Ctrl+A` again
- [ ] Verify only page 2 redirects are selected (page 1 deselected)

**Expected:** All visible redirects selected, no text selection occurs

---

### 5. Show Keyboard Shortcuts Help (?)

**Test Steps:**
- [ ] Press `Shift+/` (which produces `?`)
- [ ] Verify keyboard shortcuts dialog opens
- [ ] Verify all shortcuts are listed
- [ ] Verify shortcuts show correct modifier key (⌘ on Mac, Ctrl on Windows/Linux)
- [ ] Verify shortcuts are grouped by category
- [ ] Press `Escape` or `?` to close
- [ ] Verify dialog closes

**Expected:** Help dialog shows all available shortcuts with correct platform keys

---

### 6. Floating Keyboard Button

**Test Steps:**
- [ ] Locate floating button in bottom-right corner
- [ ] Verify keyboard icon is visible
- [ ] Hover over button - verify tooltip shows "Keyboard shortcuts (?)"
- [ ] Click button
- [ ] Verify keyboard shortcuts dialog opens
- [ ] Close dialog and verify button is still visible

**Expected:** Floating button visible, clickable, shows tooltip, opens help dialog

---

### 7. Visual Hints

**Test Steps:**
- [ ] Check Export button text includes shortcut hint (e.g., "Export (⌘E)" or "Export (Ctrl+E)")
- [ ] Check search input has hint text below: "Press Ctrl+K to focus search" (or Cmd+K)
- [ ] Verify hints show correct modifier key for platform
- [ ] Check tooltip on Export button also shows shortcut

**Expected:** All UI elements show appropriate keyboard shortcut hints

---

### 8. Platform Detection

**Test on macOS:**
- [ ] Verify shortcuts show "⌘" symbol
- [ ] Verify shortcuts use Command key (not Ctrl)
- [ ] Verify tooltips and hints show "Cmd" or "⌘"

**Test on Windows/Linux:**
- [ ] Verify shortcuts show "Ctrl"
- [ ] Verify shortcuts use Ctrl key
- [ ] Verify tooltips and hints show "Ctrl"

**Expected:** Correct modifier key detected and displayed per platform

---

### 9. Shortcuts Don't Interfere with Typing

**Test Steps:**
- [ ] Click into search input
- [ ] Type regular text including: "a", "e", "k"
- [ ] Verify typing works normally
- [ ] Verify shortcuts don't trigger while typing
- [ ] Press `Escape` while in search input
- [ ] Verify it does NOT clear or close anything (only works on modals)
- [ ] Click into a text field in Edit dialog
- [ ] Type "a", "e", "k"
- [ ] Verify typing works normally

**Expected:** Shortcuts only work when NOT typing in input fields (except Escape for modal closing)

---

### 10. Dark Mode Compatibility

**Test Steps:**
- [ ] Toggle dark mode (if available)
- [ ] Verify keyboard shortcuts dialog is readable
- [ ] Verify floating button is visible
- [ ] Verify all hints and tooltips are readable
- [ ] Verify `<kbd>` elements have proper contrast

**Expected:** All UI elements readable in both light and dark modes

---

### 11. Edge Cases

**Test Steps:**
- [ ] Open Export modal, then press `Ctrl+E` again - verify nothing breaks
- [ ] Press `Ctrl+A` with no redirects visible - verify no error
- [ ] Press `Escape` with no modal open - verify no error
- [ ] Rapidly press `?` multiple times - verify no duplicate dialogs
- [ ] Press multiple shortcuts in quick succession - verify all work

**Expected:** No errors, graceful handling of all edge cases

---

## Browser Compatibility

Test in the following browsers:
- [ ] Chrome/Chromium
- [ ] Firefox
- [ ] Safari (Mac only)
- [ ] Edge

---

## Success Criteria

All keyboard shortcuts must:
1. ✅ Work correctly without errors
2. ✅ Prevent default browser behavior where needed
3. ✅ Show correct visual feedback
4. ✅ Display correct modifier key based on platform
5. ✅ Not interfere with normal typing
6. ✅ Work in both light and dark mode
7. ✅ Show appropriate tooltips and hints

---

## Known Issues / Notes

- `useHotkeys` automatically prevents shortcuts from triggering in input fields (except when explicitly configured)
- Escape handling has priority order: Export modal > Keyboard shortcuts > Edit dialog
- `Ctrl+A` only selects visible redirects on current page (not all redirects)
- Shift+/ produces "?" character for help shortcut
