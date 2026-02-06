"""
Background worker for processing redirect mapping jobs.

This worker uses PostgreSQL LISTEN/NOTIFY for push-based job processing.
Run with: python -m backend.worker
"""

import asyncio
import traceback
import sys
import os
import socket
import uuid as uuid_module
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import json
import time

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.redirx.database import MigrationSessionDB, SupabaseClient, URLMappingDB, UserQuotaDB
from src.redirx.lib import Pipeline
from uuid import UUID
from src.redirx.config import Config

# PostgreSQL direct connection for LISTEN/NOTIFY
try:
    import psycopg
except ImportError:
    print("[Worker] ERROR: psycopg not installed. Run: pip install 'psycopg[binary]>=3.1.0'")
    sys.exit(1)


# Worker configuration from environment
WORKER_LEASE_DURATION = int(os.getenv('WORKER_LEASE_DURATION', '600'))  # 10 minutes
WORKER_MAX_CONCURRENT = int(os.getenv('WORKER_MAX_CONCURRENT', '1'))  # Process one at a time
WORKER_FALLBACK_INTERVAL = int(os.getenv('WORKER_FALLBACK_INTERVAL', '60'))  # 60 seconds
WORKER_MAX_ATTEMPTS = int(os.getenv('WORKER_MAX_ATTEMPTS', '5'))  # Max retries

# Worker identifier: hostname-pid-uuid
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{str(uuid_module.uuid4())[:8]}"


class RedirxWorker:
    """
    Push-based worker using PostgreSQL LISTEN/NOTIFY.
    """

    def __init__(self):
        self.worker_id = WORKER_ID
        self.session_db = MigrationSessionDB()
        self.running = False
        self.jobs_processed = 0
        self.pg_conn = None

    def get_database_url(self) -> Optional[str]:
        """
        Get PostgreSQL connection string from environment.

        Returns:
            DATABASE_URL connection string, or None if not set.
        """
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            print("[Worker] WARNING: DATABASE_URL not set. Falling back to polling mode.")
            print("[Worker] For push-based mode, set DATABASE_URL in .env")
            print("[Worker] Get from: Supabase Dashboard → Connect → Direct connection")
            return None
        return database_url

    def connect_postgres(self) -> Optional[psycopg.Connection]:
        """
        Establish PostgreSQL connection for LISTEN/NOTIFY.

        Returns:
            PostgreSQL connection, or None if connection fails.
        """
        database_url = self.get_database_url()
        if not database_url:
            return None

        try:
            print(f"[Worker] Connecting to PostgreSQL...")
            conn = psycopg.connect(database_url, autocommit=True)
            print("[Worker] PostgreSQL connection established")
            return conn
        except Exception as e:
            print(f"[Worker] Failed to connect to PostgreSQL: {e}")
            print("[Worker] Falling back to polling mode")
            return None

    async def claim_job(self) -> Optional[Dict[str, Any]]:
        """
        Atomically claim the next available job using RPC function.

        Returns:
            Job data if claimed, None if no jobs available.
        """
        try:
            client = SupabaseClient.get_client()
            lease_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=WORKER_LEASE_DURATION)).isoformat()

            result = client.rpc('claim_next_job', {
                'p_worker_id': self.worker_id,
                'p_lease_expires_at': lease_expires_at
            }).execute()

            if result.data and len(result.data) > 0:
                job = result.data[0]
                print(f"[Worker] Claimed job: {job['id']} (attempt {job['attempt_count']})", flush=True)
                return job

            return None

        except Exception as e:
            print(f"[Worker] Error claiming job: {e}")
            traceback.print_exc()
            return None

    async def extend_lease(self, session_id: UUID) -> bool:
        """
        Extend the lease for a running job.

        Args:
            session_id: Session ID to extend lease for.

        Returns:
            True if lease extended successfully.
        """
        try:
            client = SupabaseClient.get_client()
            new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=WORKER_LEASE_DURATION)).isoformat()

            client.table('migration_sessions').update({
                'lease_expires_at': new_expiry
            }).eq('id', str(session_id)).eq('locked_by', self.worker_id).execute()

            print(f"[Worker] Extended lease for job {session_id}")
            return True

        except Exception as e:
            print(f"[Worker] Error extending lease: {e}")
            return False

    async def release_lease(self, session_id: UUID, status: str, error_message: Optional[str] = None) -> None:
        """
        Release the lease and update job status.

        Args:
            session_id: Session ID to release.
            status: Final status ('completed', 'pending', 'permanently_failed').
            error_message: Optional error message.
        """
        try:
            client = SupabaseClient.get_client()

            updates = {
                'status': status,
                'locked_at': None,
                'locked_by': None,
                'lease_expires_at': None
            }

            if error_message:
                updates['last_error'] = error_message[:5000]

            client.table('migration_sessions').update(updates).eq(
                'id', str(session_id)
            ).execute()

            print(f"[Worker] Released lease for job {session_id}, status: {status}")

        except Exception as e:
            print(f"[Worker] Error releasing lease: {e}")

    async def process_job(self, job: Dict[str, Any]) -> bool:
        """
        Process a single job with lease management.

        Args:
            job: Job data from claim_next_job.

        Returns:
            True if successful, False if failed.
        """
        session_id = UUID(job['id'])
        attempt_count = job.get('attempt_count', 1)

        print(f"[Worker] Processing job {session_id} (attempt {attempt_count})...", flush=True)

        # Start lease extension task
        lease_extension_task = asyncio.create_task(self._lease_extension_loop(session_id))

        try:
            # Get URLs from job
            old_urls = job.get('old_urls', [])
            new_urls = job.get('new_urls', [])

            if not old_urls or not new_urls:
                print(f"[Worker] Job {session_id} has no URLs")
                await self.release_lease(session_id, 'permanently_failed', 'No URLs provided')
                return False

            print(f"[Worker] Processing {len(old_urls)} old URLs and {len(new_urls)} new URLs")

            # Run pipeline
            pipeline = Pipeline(input=(old_urls, new_urls), session_id=session_id)
            total = pipeline.total_stages
            names = pipeline.stage_names

            print(f"[Worker] Pipeline has {total} stages: {names}", flush=True)

            # Report first stage starting (progress fields were reset to NULL when job was claimed)
            print(f"[Worker] Initial progress report: stage 1/{total}, name={names[0]}", flush=True)
            self.session_db.update_session_progress(session_id, 1, names[0], total)

            final_state = None
            async for step in pipeline.iterate():
                completed = pipeline.current_stage_index
                print(f"[Worker] After iteration: completed={completed}, total={total}", flush=True)
                if completed < total:
                    print(f"[Worker] Reporting progress: stage {completed + 1}/{total}, name={names[completed]}", flush=True)
                    self.session_db.update_session_progress(session_id, completed + 1, names[completed], total)
                else:
                    print(f"[Worker] Skipping progress report (completed={completed} >= total={total})", flush=True)
                final_state = step

            # Update user usage
            user_id = job.get('user_id')
            if user_id:
                mapping_db = URLMappingDB()
                mappings = mapping_db.get_mappings_by_session(session_id)
                mapping_count = len(mappings)

                if mapping_count > 0:
                    quota_db = UserQuotaDB()
                    quota_db.increment_usage(user_id, mapping_count)
                    print(f"[Worker] Incremented usage for user {user_id} by {mapping_count}")

            # Success - release lease and mark completed
            await self.release_lease(session_id, 'completed')
            print(f"[Worker] Job {session_id} completed successfully")

            self.jobs_processed += 1
            return True

        except Exception as e:
            error_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
            print(f"[Worker] Job {session_id} failed: {e}")
            traceback.print_exc()

            # Check if we should retry or permanently fail
            if attempt_count >= WORKER_MAX_ATTEMPTS:
                print(f"[Worker] Job {session_id} exceeded max attempts, marking as permanently failed")
                await self.release_lease(session_id, 'permanently_failed', error_msg)
            else:
                print(f"[Worker] Job {session_id} will be retried")
                await self.release_lease(session_id, 'pending', error_msg)

            return False

        finally:
            # Cancel lease extension task
            lease_extension_task.cancel()
            try:
                await lease_extension_task
            except asyncio.CancelledError:
                pass

    async def _lease_extension_loop(self, session_id: UUID) -> None:
        """
        Background task to extend lease periodically.

        Args:
            session_id: Session ID to extend lease for.
        """
        try:
            while True:
                # Check every 5 minutes, extend if < 2 minutes remaining
                await asyncio.sleep(300)  # 5 minutes

                # Check current lease expiry
                try:
                    session = self.session_db.get_session(session_id)
                    lease_expires_at = session.get('lease_expires_at')

                    if lease_expires_at:
                        expiry_time = datetime.fromisoformat(lease_expires_at.replace('Z', '+00:00'))
                        time_remaining = (expiry_time - datetime.now(timezone.utc)).total_seconds()

                        if time_remaining < 120:  # Less than 2 minutes
                            await self.extend_lease(session_id)

                except Exception as e:
                    print(f"[Worker] Error checking lease expiry: {e}")

        except asyncio.CancelledError:
            pass

    async def reclaim_expired_leases(self) -> int:
        """
        Reclaim jobs with expired leases.

        Returns:
            Number of jobs reclaimed.
        """
        try:
            client = SupabaseClient.get_client()

            result = client.rpc('reclaim_expired_leases', {
                'p_max_attempts': WORKER_MAX_ATTEMPTS
            }).execute()

            count = result.data[0]['reclaimed_count'] if result.data else 0

            if count > 0:
                print(f"[Worker] Reclaimed {count} expired lease(s)")

            return count

        except Exception as e:
            print(f"[Worker] Error reclaiming expired leases: {e}")
            return 0

    async def polling_loop(self) -> None:
        """
        Fallback polling loop (when LISTEN/NOTIFY not available).
        """
        print("[Worker] Starting polling loop...")
        print(f"[Worker] Poll interval: {WORKER_FALLBACK_INTERVAL} seconds")

        self.running = True

        try:
            while self.running:
                # Try to claim a job
                job = await self.claim_job()

                if job:
                    print(f"[Worker] Claimed job: {job['id']}")
                    await self.process_job(job)
                else:
                    # No jobs, wait before polling again
                    print("[Worker] No pending jobs. Waiting...", end='\r')
                    await asyncio.sleep(WORKER_FALLBACK_INTERVAL)

        except KeyboardInterrupt:
            print("\n[Worker] Shutting down...")

    async def listen_loop(self) -> None:
        """
        Main LISTEN/NOTIFY loop for push-based job processing.
        """
        print("[Worker] Starting LISTEN loop...", flush=True)

        self.pg_conn = self.connect_postgres()

        # If connection failed, fall back to polling
        if not self.pg_conn:
            print("[Worker] PostgreSQL connection unavailable, using polling mode", flush=True)
            await self.polling_loop()
            return

        try:
            # Subscribe to job queue events
            with self.pg_conn.cursor() as cursor:
                cursor.execute("LISTEN job_queue_events")
            self.pg_conn.commit()
            print("[Worker] Subscribed to job_queue_events channel", flush=True)

            self.running = True
            last_fallback_poll = time.time()

            # Set connection to non-blocking mode for notifications
            self.pg_conn.autocommit = True

            while self.running:
                # Wait for notifications with timeout
                # In psycopg3, use notifies() generator with timeout
                gen = self.pg_conn.notifies(timeout=WORKER_FALLBACK_INTERVAL)

                try:
                    for notify in gen:
                        print(f"[Worker] Received LISTEN notification: {notify.channel}", flush=True)

                        # Try to claim and process a job
                        job = await self.claim_job()
                        if job:
                            print(f"[Worker] Claimed job via LISTEN notification: {job['id']}", flush=True)
                            await self.process_job(job)
                        break  # Exit inner loop after processing one notification
                except TimeoutError:
                    # Timeout reached, do fallback polling
                    pass

                # Fallback polling (in case we missed notifications or timeout occurred)
                now = time.time()
                if now - last_fallback_poll >= WORKER_FALLBACK_INTERVAL:
                    print("[Worker] Fallback poll check...", flush=True)
                    last_fallback_poll = now

                    # Reclaim expired leases
                    await self.reclaim_expired_leases()

                    # Try to claim a job (in case notification was missed)
                    job = await self.claim_job()
                    if job:
                        print(f"[Worker] Claimed job via fallback poll: {job['id']}", flush=True)
                        await self.process_job(job)
                    else:
                        print("[Worker] No pending jobs", end='\r', flush=True)

        except KeyboardInterrupt:
            print("\n[Worker] Shutting down...", flush=True)
        finally:
            if self.pg_conn:
                self.pg_conn.close()
                print("[Worker] PostgreSQL connection closed", flush=True)

    async def run(self) -> None:
        """
        Main entry point for the worker.
        """
        # Check if DATABASE_URL is available
        database_url = os.getenv('DATABASE_URL')
        mode = "Push-Based (LISTEN/NOTIFY)" if database_url else "Polling"

        print("=" * 60, flush=True)
        print(f"Redirx Background Worker ({mode})", flush=True)
        print("=" * 60, flush=True)
        print(f"Worker ID: {self.worker_id}", flush=True)
        print(f"Lease duration: {WORKER_LEASE_DURATION}s", flush=True)
        print(f"Max concurrent: {WORKER_MAX_CONCURRENT}", flush=True)
        print(f"Max attempts: {WORKER_MAX_ATTEMPTS}", flush=True)
        print(f"Poll interval: {WORKER_FALLBACK_INTERVAL}s", flush=True)
        if not database_url:
            print("⚠️  DATABASE_URL not set - using polling mode", flush=True)
            print("   Set DATABASE_URL for push-based notifications", flush=True)
        print("Press Ctrl+C to stop", flush=True)
        print("=" * 60, flush=True)

        try:
            # Validate configuration
            Config.validate()
            Config.validate_embeddings()

            # Start LISTEN loop (will fallback to polling if needed)
            await self.listen_loop()

        except Exception as e:
            print(f"[Worker] Fatal error: {e}")
            traceback.print_exc()
        finally:
            print(f"[Worker] Total jobs processed: {self.jobs_processed}")


def main():
    """Main entry point."""
    # Force unbuffered output for real-time logging visibility
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    worker = RedirxWorker()
    asyncio.run(worker.run())


if __name__ == '__main__':
    main()
