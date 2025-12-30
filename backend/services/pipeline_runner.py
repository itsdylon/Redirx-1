import csv
from typing import Optional, List
from urllib.parse import urlparse
from datetime import datetime
from werkzeug.datastructures import FileStorage
from src.redirx.database import MigrationSessionDB


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


def run_pipeline(
    old_csv_file: FileStorage,
    new_csv_file: FileStorage,
    user_id: str = "default_user"
) -> Optional[str]:
    """
    Queue a Redirx pipeline job for background processing.

    This function creates a session with the URLs stored in the database
    and returns immediately. The actual pipeline execution is handled
    by the background worker (backend/worker.py).

    Args:
        old_csv_file: CSV file containing old site URLs
        new_csv_file: CSV file containing new site URLs
        user_id: User ID for tracking the migration session

    Returns:
        Session ID (UUID as string) for tracking the job

    Raises:
        ValueError: If CSV files are invalid or empty
    """
    # Generate project name from new site URLs
    project_name = generate_project_name(new_csv_file)

    # Validate and read CSV files
    old_urls = read_csv(old_csv_file)
    new_urls = read_csv(new_csv_file)

    # Create migration session with URLs stored for background processing
    session_db = MigrationSessionDB()
    session_id = session_db.create_session(
        user_id=user_id,
        project_name=project_name,
        old_urls=old_urls,
        new_urls=new_urls
    )

    print(f"[API] Created job {session_id} with {len(old_urls)} old URLs and {len(new_urls)} new URLs")
    print(f"[API] Job queued for background processing (status: pending)")

    # Return session_id immediately - worker will process the job
    return str(session_id)
