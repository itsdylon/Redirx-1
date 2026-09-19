"""Process-local temporary-space admission; durable SQL remains job authority."""
from contextlib import contextmanager
from dataclasses import dataclass
import shutil
import tempfile
import threading

from src.redirx.bounded_content import MAX_TEXT_BYTES

TEMPORARY_SPACE_RESERVE_BYTES = 64 * 1024 * 1024
_lock = threading.Lock()
_reserved_bytes = 0


class PivotResourceUnavailable(RuntimeError):
    code = 'worker_capacity_unavailable'
    retryable = True

    def __init__(self, *, required_bytes, available_bytes=None):
        super().__init__('Worker temporary storage is unavailable; retry when capacity is available.')
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes


@dataclass(frozen=True)
class TemporaryCapacityReservation:
    content_bytes: int
    required_free_bytes: int
    available_bytes: int


@contextmanager
def reserve_pivot_temporary_capacity(old_count, new_count):
    """Reserve worst-case text spool bytes before a pivot pipeline starts.

    Includes other active reservations and a 64MiB free-space margin. This is
    conservative once another spool has consumed disk: its full reservation is
    still charged. It cannot reserve against unrelated processes writing later;
    actual spool failures must continue to fail the job explicitly.
    """
    global _reserved_bytes
    if any(type(count) is not int or count < 0 for count in (old_count, new_count)):
        raise ValueError('URL counts must be nonnegative integers')
    content_bytes = (old_count + new_count) * MAX_TEXT_BYTES
    with _lock:
        required = content_bytes + _reserved_bytes + TEMPORARY_SPACE_RESERVE_BYTES
        try:
            available = shutil.disk_usage(tempfile.gettempdir()).free
        except OSError as exc:
            raise PivotResourceUnavailable(required_bytes=required) from exc
        if available < required:
            raise PivotResourceUnavailable(required_bytes=required, available_bytes=available)
        _reserved_bytes += content_bytes
    try:
        yield TemporaryCapacityReservation(content_bytes, required, available)
    finally:
        with _lock:
            _reserved_bytes -= content_bytes
