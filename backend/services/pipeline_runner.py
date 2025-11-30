import csv
import asyncio
from src.redirx.lib import Pipeline

def read_csv(file_storage):
    file_storage.seek(0)
    content = file_storage.read().decode("utf-8").splitlines()

    reader = csv.reader(content)
    urls = []

    for row in reader:
        if row:
            urls.append(row[0].strip())

    return urls


def run_pipeline(old_csv_file, new_csv_file):
    old_urls = read_csv(old_csv_file)
    new_urls = read_csv(new_csv_file)

    pipeline = Pipeline(input=(old_urls, new_urls))

    async def _run_async():
        final_state = None
        async for step in pipeline.iterate():
            final_state = step
        return final_state

    old_pages, new_pages, mappings = asyncio.run(_run_async())

    session_id = old_pages[0].session_id

    return session_id
