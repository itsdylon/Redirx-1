"""Worker-local database transport; only explicit SELECT reads may be retried."""
from contextlib import contextmanager
import time
import httpx
from postgrest import SyncPostgrestClient
from src.redirx.config import Config


@contextmanager
def jev_worker_database(*, transport=None):
    # Isolate the six Jev page threads from background service connections.
    # No HTTP/2 multiplexing or idle-connection reuse; mutations have zero retries.
    owned_transport=transport or httpx.HTTPTransport(
        http1=True,http2=False,retries=0,
        limits=httpx.Limits(max_connections=8,max_keepalive_connections=0))
    with httpx.Client(transport=owned_transport,timeout=30,follow_redirects=False) as http:
        yield SyncPostgrestClient(Config.SUPABASE_URL.rstrip('/')+'/rest/v1',
            headers={'apikey':Config.SUPABASE_KEY,'Authorization':'Bearer '+Config.SUPABASE_KEY},
            http_client=http)


def database_read(operation):
    """At most two attempts for an explicitly read-only query, never an RPC."""
    for attempt in range(2):
        try:
            return operation()
        except (httpx.ReadError,httpx.ReadTimeout,httpx.RemoteProtocolError):
            if attempt:raise
            time.sleep(.1)
