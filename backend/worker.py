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
from backend.services.deep_preview_service import DeepPreviewService
from backend.services.job_limits import (
    ContentJobUrlCapExceeded,
    validate_content_job_url_counts,
)

# PostgreSQL direct connection for LISTEN/NOTIFY
try:
    import psycopg
except ImportError:
    print("[Worker] ERROR: psycopg not installed. Run: pip install 'psycopg[binary]>=3.1.0'")
    sys.exit(1)


# Worker configuration from environment
def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an integer env var and clamp it into [minimum, maximum]."""
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        print(
            f"[Worker] Invalid {name}={raw!r}; using default {default}",
            flush=True,
        )
        value = default

    if value < minimum:
        print(
            f"[Worker] {name}={value} below minimum {minimum}; clamping",
            flush=True,
        )
        return minimum
    if value > maximum:
        print(
            f"[Worker] {name}={value} above maximum {maximum}; clamping",
            flush=True,
        )
        return maximum
    return value


WORKER_LEASE_DURATION = int(os.getenv('WORKER_LEASE_DURATION', '600'))  # 10 minutes
WORKER_MAX_CONCURRENT = _bounded_env_int('WORKER_MAX_CONCURRENT', 1, 1, 32)
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
        self.max_concurrent = WORKER_MAX_CONCURRENT
        self.in_flight_tasks: set[asyncio.Task] = set()
        self.pg_conn = None
        self.pg_claim_conn = None

    def _apply_usage_accounting(
        self,
        user_id: Optional[str],
        mapping_count: int,
        pipeline_type: str,
        is_preview: bool,
    ) -> None:
        """
        Apply usage accounting after a successful job completion.

        Rules:
        - Preview jobs are always non-billable.
        - url_only source jobs increment Quick Match usage.
        - content source jobs increment Deep Match credits.
        """
        if not user_id or mapping_count <= 0:
            return

        if is_preview:
            print(f"[Worker] Preview job is non-billable; skipped usage increment")
            return

        quota_db = UserQuotaDB()
        if pipeline_type == 'url_only':
            quota_db.increment_quick_match_usage(user_id, mapping_count)
            print(f"[Worker] Incremented Quick Match usage for user {user_id} by {mapping_count}")
        else:
            quota_db.increment_credits(user_id, mapping_count)
            print(f"[Worker] Incremented Deep Match credits for user {user_id} by {mapping_count}")

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
        lease_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=WORKER_LEASE_DURATION)).isoformat()

        # Prefer direct PostgreSQL claim when available (LISTEN/NOTIFY mode).
        # This avoids PostgREST/RPC visibility edge cases.
        pg_claim = self._claim_job_via_postgres(lease_expires_at)
        if pg_claim:
            print(f"[Worker] Claimed job via PostgreSQL: {pg_claim['id']} (attempt {pg_claim['attempt_count']})", flush=True)
            return pg_claim

        try:
            client = SupabaseClient.get_client()

            result = client.rpc('claim_next_job', {
                'p_worker_id': self.worker_id,
                'p_lease_expires_at': lease_expires_at
            }).execute()

            if result.data and len(result.data) > 0:
                job = result.data[0]
                print(f"[Worker] Claimed job: {job['id']} (attempt {job['attempt_count']})", flush=True)
                return job
            # Fallback: if RPC returns nothing, try direct claim for environments
            # where claim_next_job is missing/misconfigured.
            return self._claim_job_fallback(client, lease_expires_at)

        except Exception as e:
            print(f"[Worker] Error claiming job: {e}")
            traceback.print_exc()
            try:
                client = SupabaseClient.get_client()
                return self._claim_job_fallback(client, lease_expires_at)
            except Exception as fallback_error:
                print(f"[Worker] Fallback claim also failed: {fallback_error}")
                return None

    def _claim_job_via_postgres(self, lease_expires_at: str) -> Optional[Dict[str, Any]]:
        """
        Claim a job directly through PostgreSQL to avoid RPC visibility/permission gaps.
        Keeps backward compatibility with older claim_next_job return signatures.
        """
        if not self.pg_claim_conn:
            return None

        try:
            with self.pg_claim_conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM claim_next_job(%s, %s)",
                    (self.worker_id, lease_expires_at),
                )
                row = cursor.fetchone()
                if not row:
                    return None

                columns = [desc.name for desc in cursor.description]
                claimed = dict(zip(columns, row))
                claimed_id = claimed.get('id')
                if isinstance(claimed_id, UUID):
                    claimed_id = str(claimed_id)

                claimed_source = claimed.get('source_session_id')
                if isinstance(claimed_source, UUID):
                    claimed_source = str(claimed_source)

                job = {
                    'id': claimed_id,
                    'user_id': claimed.get('user_id'),
                    'project_name': claimed.get('project_name'),
                    'old_urls': claimed.get('old_urls') or [],
                    'new_urls': claimed.get('new_urls') or [],
                    'attempt_count': int(claimed.get('attempt_count') or 1),
                    'pipeline_type': claimed.get('pipeline_type') or 'content',
                    'is_preview': bool(claimed.get('is_preview', False)),
                    'source_session_id': claimed_source,
                }
                return job
        except Exception as e:
            print(f"[Worker] PostgreSQL claim failed: {e}", flush=True)
            return None

    def _claim_job_fallback(self, client, lease_expires_at: str) -> Optional[Dict[str, Any]]:
        """
        Direct table-based claim fallback when claim_next_job RPC returns no row.
        Uses optimistic status check for safe single-row claim.
        """
        pending = client.table('migration_sessions').select(
            'id,user_id,project_name,old_urls,new_urls,attempt_count,pipeline_type,is_preview,source_session_id'
        ).eq(
            'status', 'pending'
        ).order(
            'created_at', desc=False
        ).limit(1).execute()

        if not pending.data:
            return None

        candidate = pending.data[0]
        current_attempt = int(candidate.get('attempt_count') or 0)
        new_attempt = current_attempt + 1

        update_result = client.table('migration_sessions').update({
            'status': 'processing',
            'locked_at': datetime.now(timezone.utc).isoformat(),
            'locked_by': self.worker_id,
            'lease_expires_at': lease_expires_at,
            'attempt_count': new_attempt,
            'current_stage': None,
            'stage_name': None,
            'total_stages': None,
        }).eq(
            'id', candidate['id']
        ).eq(
            'status', 'pending'
        ).execute()

        if not update_result.data:
            print("[Worker] Fallback claim saw pending row, but it was claimed concurrently", flush=True)
            return None

        job = {
            'id': candidate['id'],
            'user_id': candidate.get('user_id'),
            'project_name': candidate.get('project_name'),
            'old_urls': candidate.get('old_urls') or [],
            'new_urls': candidate.get('new_urls') or [],
            'attempt_count': new_attempt,
            'pipeline_type': candidate.get('pipeline_type') or 'content',
            'is_preview': bool(candidate.get('is_preview', False)),
            'source_session_id': candidate.get('source_session_id'),
        }
        print(
            f"[Worker] Claimed job via fallback: {job['id']} (attempt {job['attempt_count']})",
            flush=True,
        )
        return job

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
        pipeline_type = job.get('pipeline_type', 'content')
        is_preview = bool(job.get('is_preview', False))

        print(
            f"[Worker] Processing job {session_id} (attempt {attempt_count}, "
            f"pipeline={pipeline_type}, preview={is_preview})...",
            flush=True
        )

        # Start lease extension task
        lease_extension_task = asyncio.create_task(self._lease_extension_loop(session_id))
        preview_service = DeepPreviewService()

        try:
            # Get URLs from job
            old_urls = job.get('old_urls', [])
            new_urls = job.get('new_urls', [])

            if not old_urls or not new_urls:
                print(f"[Worker] Job {session_id} has no URLs")
                if is_preview:
                    preview_service.mark_failed(session_id, 'No URLs provided')
                await self.release_lease(session_id, 'permanently_failed', 'No URLs provided')
                return False

            try:
                validate_content_job_url_counts(old_urls, new_urls, pipeline_type)
            except ContentJobUrlCapExceeded as cap_error:
                fail_message = cap_error.to_worker_error_message()
                print(f"[Worker] Job {session_id} rejected before processing: {fail_message}")
                if is_preview:
                    preview_service.mark_failed(session_id, fail_message)
                await self.release_lease(session_id, 'permanently_failed', fail_message)
                return False

            print(f"[Worker] Processing {len(old_urls)} old URLs and {len(new_urls)} new URLs (pipeline: {pipeline_type})")

            # Only validate OpenAI key for content pipeline (url_only doesn't need it)
            if pipeline_type == 'content':
                Config.validate_embeddings()

            if is_preview:
                preview_service.mark_processing(session_id)

            # Run pipeline
            pipeline = Pipeline(input=(old_urls, new_urls), session_id=session_id, pipeline_type=pipeline_type)
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
            mapping_count = 0
            if user_id:
                mapping_db = URLMappingDB()
                mappings = mapping_db.get_mappings_by_session(session_id)
                mapping_count = len(mappings or [])

                self._apply_usage_accounting(
                    user_id=user_id,
                    mapping_count=mapping_count,
                    pipeline_type=pipeline_type,
                    is_preview=is_preview,
                )

            # Queue preview job after successful source url_only completion.
            if (not is_preview) and pipeline_type == 'url_only' and user_id:
                try:
                    preview_result = preview_service.maybe_queue_preview(
                        source_session_id=session_id,
                        user_id=user_id,
                        old_urls=old_urls,
                        new_urls=new_urls,
                    )
                    print(f"[Worker] Deep preview queue result for {session_id}: {preview_result}")
                except Exception as preview_err:
                    print(f"[Worker] Failed to queue deep preview for {session_id}: {preview_err}")

            # Finalize preview snapshot after preview content job completes.
            if is_preview:
                try:
                    finalize_result = preview_service.finalize_preview(session_id)
                    print(f"[Worker] Deep preview finalize result for {session_id}: {finalize_result}")
                except Exception as preview_err:
                    print(f"[Worker] Failed to finalize deep preview for {session_id}: {preview_err}")
                    preview_service.mark_failed(session_id, str(preview_err))

            # Success - release lease and mark completed
            await self.release_lease(session_id, 'completed')
            print(f"[Worker] Job {session_id} completed successfully")

            # Send completion email (fire-and-forget)
            if not is_preview:
                try:
                    from backend.services.email_service import EmailService
                    if user_id:
                        client = SupabaseClient.get_client()
                        profile = client.table('user_profiles').select(
                            'email'
                        ).eq('id', user_id).maybe_single().execute()
                        if profile.data and profile.data.get('email'):
                            project_name = job.get('project_name', 'Migration')
                            old_domain = ""
                            new_domain = ""
                            if old_urls:
                                try:
                                    from urllib.parse import urlparse
                                    old_domain = urlparse(old_urls[0]).netloc
                                except Exception:
                                    pass
                            if new_urls:
                                try:
                                    from urllib.parse import urlparse
                                    new_domain = urlparse(new_urls[0]).netloc
                                except Exception:
                                    pass
                            email_svc = EmailService()
                            email_svc.send_mapping_complete(
                                user_id=user_id,
                                to_email=profile.data['email'],
                                project_name=project_name,
                                total_mappings=mapping_count,
                                session_id=str(session_id),
                                old_site_domain=old_domain,
                                new_site_domain=new_domain,
                            )
                except Exception:
                    print(f"[Worker] Completion email failed (non-blocking)")

            self.jobs_processed += 1
            return True

        except Exception as e:
            error_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
            print(f"[Worker] Job {session_id} failed: {e}")
            traceback.print_exc()

            if is_preview:
                try:
                    preview_service.mark_failed(session_id, str(e))
                except Exception:
                    print(f"[Worker] Failed to persist preview failure status for {session_id}")

            # Check if we should retry or permanently fail
            if attempt_count >= WORKER_MAX_ATTEMPTS:
                print(f"[Worker] Job {session_id} exceeded max attempts, marking as permanently failed")
                await self.release_lease(session_id, 'permanently_failed', error_msg)

                # Send failure email (fire-and-forget)
                if not is_preview:
                    try:
                        from backend.services.email_service import EmailService
                        user_id = job.get('user_id')
                        if user_id:
                            client = SupabaseClient.get_client()
                            profile = client.table('user_profiles').select(
                                'email'
                            ).eq('id', user_id).maybe_single().execute()
                            if profile.data and profile.data.get('email'):
                                project_name = job.get('project_name', 'Migration')
                                email_svc = EmailService()
                                email_svc.send_mapping_failed(
                                    user_id=user_id,
                                    to_email=profile.data['email'],
                                    project_name=project_name,
                                    error_summary=str(e),
                                    session_id=str(session_id),
                                )
                    except Exception:
                        print(f"[Worker] Failure email failed (non-blocking)")
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

    def _has_available_capacity(self) -> bool:
        """Return True when this worker can accept another in-flight job."""
        return len(self.in_flight_tasks) < self.max_concurrent

    def _start_in_flight_job(self, job: Dict[str, Any], source: str) -> None:
        """Start processing a claimed job as an in-flight task."""
        task = asyncio.create_task(self.process_job(job))
        self.in_flight_tasks.add(task)

        print(
            f"[Worker] Dispatched job {job.get('id')} via {source} "
            f"({len(self.in_flight_tasks)}/{self.max_concurrent} in-flight)",
            flush=True,
        )

    async def _reap_finished_jobs(
        self,
        wait_for_one: bool = False,
        timeout: Optional[float] = None,
    ) -> int:
        """
        Remove completed in-flight tasks and surface any unexpected exceptions.
        """
        if not self.in_flight_tasks:
            return 0

        if wait_for_one:
            done, _ = await asyncio.wait(
                self.in_flight_tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        else:
            done = {task for task in self.in_flight_tasks if task.done()}

        if not done:
            return 0

        for task in done:
            self.in_flight_tasks.discard(task)
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[Worker] In-flight job task raised unexpectedly: {e}", flush=True)

        return len(done)

    async def _dispatch_until_capacity(self, source: str) -> int:
        """
        Claim and dispatch jobs until the worker reaches max in-flight capacity.
        """
        claimed = 0
        while self.running and self._has_available_capacity():
            job = await self.claim_job()
            if not job:
                break
            self._start_in_flight_job(job, source)
            claimed += 1
        return claimed

    async def _wait_for_in_flight_jobs(self) -> None:
        """Drain in-flight jobs before shutdown."""
        while self.in_flight_tasks:
            await self._reap_finished_jobs(wait_for_one=True)

    async def polling_loop(self) -> None:
        """
        Fallback polling loop (when LISTEN/NOTIFY not available).
        """
        print("[Worker] Starting polling loop...")
        print(f"[Worker] Poll interval: {WORKER_FALLBACK_INTERVAL} seconds")

        self.running = True

        try:
            while self.running:
                await self._reap_finished_jobs(wait_for_one=False)
                claimed = await self._dispatch_until_capacity("polling loop")

                if claimed > 0:
                    continue

                if self.in_flight_tasks:
                    await self._reap_finished_jobs(wait_for_one=True, timeout=1.0)
                else:
                    # No jobs, wait before polling again
                    print("[Worker] No pending jobs. Waiting...", end='\r')
                    await asyncio.sleep(WORKER_FALLBACK_INTERVAL)

        except KeyboardInterrupt:
            self.running = False
            print("\n[Worker] Shutting down...")
        finally:
            await self._wait_for_in_flight_jobs()

    async def listen_loop(self) -> None:
        """
        Main LISTEN/NOTIFY loop for push-based job processing.
        """
        print("[Worker] Starting LISTEN loop...", flush=True)

        self.pg_conn = self.connect_postgres()
        self.pg_claim_conn = self.connect_postgres()

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
                await self._reap_finished_jobs(wait_for_one=False)

                # Opportunistically fill all available capacity.
                claimed = await self._dispatch_until_capacity("LISTEN dispatch")

                # Fallback polling (in case we missed notifications or timeout occurred)
                now = time.time()
                if now - last_fallback_poll >= WORKER_FALLBACK_INTERVAL:
                    print("[Worker] Fallback poll check...", flush=True)
                    last_fallback_poll = now

                    # Reclaim expired leases
                    await self.reclaim_expired_leases()

                    # Try to claim jobs (in case notifications were missed).
                    claimed += await self._dispatch_until_capacity("fallback poll")

                if claimed > 0:
                    continue

                if self.in_flight_tasks:
                    # Keep the dispatch loop responsive while work is in progress.
                    await self._reap_finished_jobs(wait_for_one=True, timeout=1.0)
                    continue

                # No in-flight work and nothing was claimable; block on LISTEN.
                gen = self.pg_conn.notifies(timeout=WORKER_FALLBACK_INTERVAL)
                try:
                    for notify in gen:
                        print(f"[Worker] Received LISTEN notification: {notify.channel}", flush=True)
                        break
                except TimeoutError:
                    print("[Worker] No pending jobs", end='\r', flush=True)

        except KeyboardInterrupt:
            self.running = False
            print("\n[Worker] Shutting down...", flush=True)
        finally:
            await self._wait_for_in_flight_jobs()
            if self.pg_conn:
                self.pg_conn.close()
                print("[Worker] PostgreSQL connection closed", flush=True)
            if self.pg_claim_conn:
                self.pg_claim_conn.close()
                print("[Worker] PostgreSQL claim connection closed", flush=True)

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
        print(f"Max concurrent: {self.max_concurrent}", flush=True)
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
            # Note: OpenAI key is validated per-job in process_job for content pipelines only

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
