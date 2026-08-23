import csv
import hashlib
from typing import Optional, List
from urllib.parse import urlparse
from datetime import datetime
from werkzeug.datastructures import FileStorage
from src.redirx.database import MigrationSessionDB
from backend.services.job_limits import validate_content_job_url_counts


def read_csv(file_storage: FileStorage) -> List[str]:
    """
    Read URLs from a CSV file.

    Args:
        file_storage: Flask FileStorage object containing CSV data

    Returns:
        List of URLs (first column of each row)

    Raises:
        ValueError: If file is empty or invalid
    """
    try:
        file_storage.seek(0)
        content = file_storage.read().decode("utf-8").splitlines()

        if not content:
            raise ValueError("CSV file is empty")

        reader = csv.reader(content)
        urls = []

        for row in reader:
            if row and row[0].strip():
                urls.append(row[0].strip())

        if not urls:
            raise ValueError("No valid URLs found in CSV file")

        return urls

    except UnicodeDecodeError as e:
        raise ValueError(f"Invalid file encoding: {e}")
    except Exception as e:
        raise ValueError(f"Failed to read CSV file: {e}")


def generate_project_name(new_csv_file: FileStorage) -> str:
    """
    Generate a project name based on the new site's base URL.

    Strategy:
    1. Read first 10 rows of new CSV file
    2. Find first valid URL in the URL column
    3. Extract base domain using urlparse(url).netloc
    4. Strip "www." prefix if present
    5. Format as "{domain} project"
    6. Fallback to "Redirect Project {timestamp}" if no valid URL

    Args:
        new_csv_file: CSV file containing new site URLs

    Returns:
        Generated project name string

    Examples:
        "https://www.newsite.com/page" → "newsite.com project"
        "https://blog.company.io/article" → "blog.company.io project"
        No valid URLs → "Redirect Project 2024-12-28 15:30:45"
    """
    try:
        # Save current position
        new_csv_file.seek(0)
        content = new_csv_file.read().decode("utf-8").splitlines()

        # Reset file pointer for later use
        new_csv_file.seek(0)

        if not content:
            return f"Redirect Project {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        reader = csv.reader(content)

        # Try to extract domain from first 10 valid URLs
        for i, row in enumerate(reader):
            if i >= 10:
                break

            if row and row[0].strip():
                url = row[0].strip()

                try:
                    parsed = urlparse(url)
                    domain = parsed.netloc

                    if domain:
                        # Strip "www." prefix if present
                        if domain.startswith('www.'):
                            domain = domain[4:]

                        return f"{domain} project"
                except Exception:
                    # Skip invalid URLs
                    continue

        # Fallback if no valid URL found
        return f"Redirect Project {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    except Exception as e:
        # Fallback on any error
        return f"Redirect Project {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"


def generate_deterministic_key(user_id: str, old_urls: List[str], new_urls: List[str], pipeline_type: str = 'content') -> str:
    """
    Generate a deterministic idempotency key from user_id, URLs, and pipeline type.

    Args:
        user_id: User identifier
        old_urls: List of old site URLs
        new_urls: List of new site URLs
        pipeline_type: Pipeline type ('content' or 'url_only')

    Returns:
        SHA256 hash as hex string
    """
    # Sort URLs to ensure consistency
    sorted_old = sorted(old_urls)
    sorted_new = sorted(new_urls)

    # Create deterministic string (includes pipeline_type to avoid collisions)
    key_data = f"{user_id}|{pipeline_type}|{','.join(sorted_old)}|{','.join(sorted_new)}"

    # Hash it
    return hashlib.sha256(key_data.encode('utf-8')).hexdigest()


def run_pipeline(
    old_csv_file: FileStorage,
    new_csv_file: FileStorage,
    user_id: str = "default_user",
    force: bool = False,
    pipeline_type: str = 'content',
    old_urls: Optional[List[str]] = None,
    new_urls: Optional[List[str]] = None,
    priority: int = 0,
) -> tuple[Optional[str], bool]:
    """
    Queue a Redirx pipeline job for background processing.

    This function creates a session with the URLs stored in the database
    and returns immediately. The actual pipeline execution is handled
    by the background worker (backend/worker.py).

    Implements idempotency: submitting identical URLs will return the existing
    session instead of creating a new one (unless force=True).

    Args:
        old_csv_file: CSV file containing old site URLs
        new_csv_file: CSV file containing new site URLs
        user_id: User ID for tracking the migration session
        force: If True, bypass idempotency check and create a new job
        pipeline_type: 'content' (default) or 'url_only' (free tier)
        old_urls: Optional pre-parsed old URL list (skips CSV parsing when provided)
        new_urls: Optional pre-parsed new URL list (skips CSV parsing when provided)
        priority: Queue priority for a newly created session (migration 027).

    Returns:
        Tuple of (session_id, is_duplicate) where:
        - session_id: UUID as string for tracking the job
        - is_duplicate: True if returning existing session, False if created new

    Raises:
        ValueError: If CSV files are invalid or empty
    """
    # Generate project name from new site URLs
    project_name = generate_project_name(new_csv_file)

    # Validate and read CSV files (or reuse pre-parsed lists)
    if old_urls is None:
        old_urls = read_csv(old_csv_file)
    if new_urls is None:
        new_urls = read_csv(new_csv_file)

    # Hard-cap content jobs before idempotency/session creation.
    validate_content_job_url_counts(old_urls, new_urls, pipeline_type)

    # Generate idempotency key (or None if force=True to bypass uniqueness constraint)
    idempotency_key = None if force else generate_deterministic_key(user_id, old_urls, new_urls, pipeline_type)

    # Check if session already exists for this exact request (unless force=True)
    session_db = MigrationSessionDB()

    if not force and idempotency_key:
        existing_session = session_db.find_session_by_idempotency_key(user_id, idempotency_key)

        if existing_session:
            session_id = existing_session['id']
            status = existing_session['status']
            print(f"[API] Found existing session {session_id} with status: {status}", flush=True)
            print(f"[API] Returning existing session (idempotency key matched)", flush=True)
            return str(session_id), True  # is_duplicate=True

    if force:
        print(f"[API] Force=True, bypassing idempotency (key set to NULL)", flush=True)

    # Create new migration session with URLs stored for background processing
    session_id = session_db.create_session(
        user_id=user_id,
        project_name=project_name,
        old_urls=old_urls,
        new_urls=new_urls,
        idempotency_key=idempotency_key,
        pipeline_type=pipeline_type,
        priority=priority,
    )

    print(f"[API] Created job {session_id} with {len(old_urls)} old URLs and {len(new_urls)} new URLs", flush=True)
    print(f"[API] Job queued for background processing (status: pending)", flush=True)

    # Return session_id immediately - worker will process the job
    return str(session_id), False  # is_duplicate=False
