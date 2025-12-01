import csv
import asyncio
import traceback
from uuid import uuid4
from typing import Optional, Tuple, List
from werkzeug.datastructures import FileStorage
from src.redirx.lib import Pipeline
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


def run_pipeline(
    old_csv_file: FileStorage,
    new_csv_file: FileStorage,
    user_id: str = "default_user"
) -> Optional[str]:
    """
    Run the Redirx pipeline on CSV files containing old and new site URLs.

    Args:
        old_csv_file: CSV file containing old site URLs
        new_csv_file: CSV file containing new site URLs
        user_id: User ID for tracking the migration session

    Returns:
        Session ID (UUID as string) or None if pipeline fails

    Raises:
        ValueError: If CSV files are invalid or empty
        RuntimeError: If pipeline execution fails
    """
    # Validate and read CSV files
    old_urls = read_csv(old_csv_file)
    new_urls = read_csv(new_csv_file)

    # Create migration session
    session_db = MigrationSessionDB()
    session_id = session_db.create_session(user_id=user_id)

    try:
        # Create pipeline with session_id
        pipeline = Pipeline(
            input=(old_urls, new_urls),
            session_id=session_id
        )

        # Run pipeline asynchronously
        async def _run_async():
            final_state = None
            async for step in pipeline.iterate():
                final_state = step
            return final_state

        old_pages, new_pages, mappings = asyncio.run(_run_async())

        # Return session_id directly from pipeline
        return str(pipeline.session_id)

    except Exception as e:
        # Log error with full traceback
        print(f"\n{'='*60}")
        print(f"PIPELINE EXECUTION FAILED")
        print(f"{'='*60}")
        print(f"Error: {e}")
        print(f"\nFull traceback:")
        traceback.print_exc()
        print(f"{'='*60}\n")
        raise RuntimeError(f"Pipeline execution failed: {e}")
