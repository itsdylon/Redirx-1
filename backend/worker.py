"""
Background worker for processing redirect mapping jobs.

This worker polls the database for pending jobs and processes them.
Run with: python -m backend.worker
"""

import time
import asyncio
import traceback
import sys
import os

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.redirx.database import MigrationSessionDB, SupabaseClient, URLMappingDB, UserQuotaDB
from src.redirx.lib import Pipeline
from uuid import UUID


POLL_INTERVAL = 5  # seconds between polling for new jobs


def process_job(session: dict) -> bool:
    """
    Process a single pending job.

    Args:
        session: Session data from database including old_urls and new_urls.

    Returns:
        True if successful, False if failed.
    """
    session_db = MigrationSessionDB()
    session_id = UUID(session['id'])

    print(f"[Worker] Processing job {session_id}...")

    try:
        # Update status to processing
        session_db.update_session_status(session_id, 'processing')

        # Get URLs from session
        old_urls = session.get('old_urls', [])
        new_urls = session.get('new_urls', [])

        if not old_urls or not new_urls:
            print(f"[Worker] Job {session_id} has no URLs, marking as failed")
            session_db.update_session_status(session_id, 'failed')
            return False

        print(f"[Worker] Processing {len(old_urls)} old URLs and {len(new_urls)} new URLs")

        # Run pipeline
        pipeline = Pipeline(input=(old_urls, new_urls), session_id=session_id)

        async def _run():
            final_state = None
            async for step in pipeline.iterate():
                final_state = step
            return final_state

        asyncio.run(_run())

        # Count mappings created and update user usage
        user_id = session.get('user_id')
        if user_id:
            mapping_db = URLMappingDB()
            mappings = mapping_db.get_mappings_by_session(session_id)
            mapping_count = len(mappings)

            if mapping_count > 0:
                quota_db = UserQuotaDB()
                quota_db.increment_usage(user_id, mapping_count)
                print(f"[Worker] Incremented usage for user {user_id} by {mapping_count}")

        # Update status to completed
        session_db.update_session_status(session_id, 'completed')
        print(f"[Worker] Job {session_id} completed successfully")
        return True

    except Exception as e:
        print(f"[Worker] Job {session_id} failed with error: {e}")
        traceback.print_exc()

        try:
            session_db.update_session_status(session_id, 'failed')
        except Exception:
            print(f"[Worker] Failed to update status to 'failed' for {session_id}")

        return False


def get_next_pending_job() -> dict | None:
    """
    Get the next pending job from the database.

    Returns:
        Session data dict if a pending job exists, None otherwise.
    """
    client = SupabaseClient.get_client()

    result = client.table('migration_sessions') \
        .select('*') \
        .eq('status', 'pending') \
        .order('created_at') \
        .limit(1) \
        .execute()

    if result.data:
        return result.data[0]

    return None


def main():
    """Main worker loop - polls for pending jobs and processes them."""
    print("=" * 60)
    print("Redirx Background Worker")
    print("=" * 60)
    print(f"Polling interval: {POLL_INTERVAL} seconds")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    jobs_processed = 0

    try:
        while True:
            job = get_next_pending_job()

            if job:
                jobs_processed += 1
                print(f"\n[Worker] Found pending job: {job['id']}")
                print(f"[Worker] Project: {job.get('project_name', 'Unnamed')}")
                success = process_job(job)
                status = "SUCCESS" if success else "FAILED"
                print(f"[Worker] Job {status}. Total processed: {jobs_processed}")
            else:
                # No jobs, wait before polling again
                print(f"[Worker] No pending jobs. Waiting {POLL_INTERVAL}s...", end='\r')
                time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n[Worker] Shutting down...")
        print(f"[Worker] Total jobs processed: {jobs_processed}")


if __name__ == '__main__':
    main()
