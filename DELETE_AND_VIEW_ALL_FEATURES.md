# Delete and View All Projects Features

## Summary
Added two new features to the Redirx dashboard:
1. **Delete button** - Delete projects from the database with confirmation dialog
2. **View All Projects** - Navigate to a dedicated page showing all projects with search functionality

## Changes Made

### Backend Changes

#### 1. `/backend/routes/user_routes.py`
- Added `DELETE /api/user/sessions/<session_id>` endpoint
- Endpoint verifies user ownership before deletion
- Cascades deletion to:
  - `url_mappings` (redirect mappings)
  - `webpage_embeddings` (vector embeddings)
  - `migration_sessions` (the session itself)

### Frontend Changes

#### 2. `/frontend/src/api/sessions.ts`
Added two new API functions:
- `deleteSession(sessionId)` - Calls DELETE endpoint to remove a project
- `fetchAllSessions()` - Calls GET endpoint to retrieve all user sessions

#### 3. `/frontend/src/components/Dashboard.tsx`
- Added **delete button** to each project row (trash icon)
- Added **"View All Projects"** button above the Recent Projects table
- Implemented confirmation dialog with:
  - Warning message about permanent deletion
  - Cancel and Delete buttons
  - State management for delete confirmation
- Added `Trash2` icon import from lucide-react
- Added delete handler functions:
  - `handleDeleteClick()` - Shows confirmation dialog
  - `handleConfirmDelete()` - Executes deletion and updates UI
  - `handleCancelDelete()` - Dismisses dialog

#### 4. `/frontend/src/components/AllProjects.tsx` (NEW FILE)
Created new full-page component with:
- **Search functionality** - Filter projects by name or status
- **All projects list** - Shows complete project history (not just recent 5)
- **Inline editing** - Rename projects directly in the table
- **Delete functionality** - Same confirmation dialog as dashboard
- **Status indicators** - Visual badges for pending/processing/completed/failed
- **Back to Dashboard** button for navigation

#### 5. `/frontend/src/App.tsx`
- Added import for `AllProjects` component
- Added route: `/projects` → `<AllProjects />`
- Added explicit route: `/dashboard` → `<Dashboard />` (for consistency)

## User Flow

### Deleting a Project
1. User clicks the trash icon next to any project
2. Confirmation dialog appears with warning message
3. User can either:
   - Click "Cancel" to dismiss
   - Click "Delete Project" to confirm
4. On confirmation:
   - API call deletes the project and all associated data
   - UI updates to remove the project from the list
   - No page refresh needed (optimistic UI update)

### Viewing All Projects
1. User clicks "View All Projects" button on dashboard
2. Navigate to `/projects` page
3. See complete list of all projects with search bar
4. Can search by project name or status
5. Can edit names, delete projects, or view details
6. Click "Back to Dashboard" to return

## Security
- All endpoints protected by `@require_auth` decorator
- User ownership verified before deletion
- Row Level Security (RLS) policies enforce multi-tenancy
- Only the project owner can delete their own projects

## Database Impact
When a project is deleted, the following records are removed:
1. All `url_mappings` for that session
2. All `webpage_embeddings` for that session
3. The `migration_sessions` record itself

**Note:** If foreign key constraints with `ON DELETE CASCADE` are configured in the database schema, the manual deletion of url_mappings and webpage_embeddings could be replaced with automatic cascading. Current implementation explicitly deletes each table for clarity and to ensure deletion even without CASCADE constraints.

## Testing Checklist
- [x] Build completes without TypeScript errors
- [ ] Delete confirmation dialog appears on button click
- [ ] Delete button removes project from database
- [ ] View All Projects button navigates to `/projects`
- [ ] Search functionality filters projects correctly
- [ ] Delete button works on both Dashboard and All Projects pages
- [ ] Unauthorized users cannot delete other users' projects (403 error)
- [ ] Deleting non-existent project returns 404 error

## Future Enhancements
- Add bulk delete functionality (select multiple projects)
- Add project export before deletion
- Add "restore" functionality (soft delete with trash bin)
- Add project archiving (hide without deletion)
- Add filters (by status, date range, etc.)
- Add sorting options (by name, date, redirects, etc.)
- Add pagination for users with many projects
