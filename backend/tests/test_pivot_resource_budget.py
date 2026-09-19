import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from backend.services import pivot_resource_budget as budget


class TemporaryAdmissionTests(unittest.TestCase):
    def test_full_size_exact_boundary_and_one_byte_short(self):
        required = 35000 * 32000 + budget.TEMPORARY_SPACE_RESERVE_BYTES
        with patch.object(budget.shutil, 'disk_usage', return_value=SimpleNamespace(free=required)):
            with budget.reserve_pivot_temporary_capacity(15000, 20000) as reservation:
                self.assertEqual(reservation.required_free_bytes, required)
        with patch.object(budget.shutil, 'disk_usage', return_value=SimpleNamespace(free=required-1)):
            with self.assertRaises(budget.PivotResourceUnavailable) as error:
                with budget.reserve_pivot_temporary_capacity(15000, 20000):
                    self.fail('insufficient storage admitted')
            self.assertTrue(error.exception.retryable)
            self.assertEqual(error.exception.code, 'worker_capacity_unavailable')

    def test_concurrent_reservations_and_cancel_release(self):
        free = 35000 * 32000 + budget.TEMPORARY_SPACE_RESERVE_BYTES
        with patch.object(budget.shutil, 'disk_usage', return_value=SimpleNamespace(free=free)):
            with self.assertRaises(asyncio.CancelledError):
                with budget.reserve_pivot_temporary_capacity(15000, 20000):
                    with self.assertRaises(budget.PivotResourceUnavailable):
                        with budget.reserve_pivot_temporary_capacity(1, 0):
                            self.fail('overlapping reservation admitted')
                    raise asyncio.CancelledError()
            with budget.reserve_pivot_temporary_capacity(15000, 20000):
                pass
        self.assertEqual(budget._reserved_bytes, 0)

    def test_unknown_filesystem_fails_closed_and_invalid_counts_do_not_reserve(self):
        with patch.object(budget.shutil, 'disk_usage', side_effect=OSError('fixture')):
            with self.assertRaises(budget.PivotResourceUnavailable) as error:
                with budget.reserve_pivot_temporary_capacity(1, 1):
                    self.fail('unknown storage admitted')
            self.assertIsNone(error.exception.available_bytes)
        for counts in [(True, 0), (-1, 0), (1.5, 0)]:
            with self.assertRaises(ValueError):
                with budget.reserve_pivot_temporary_capacity(*counts):
                    pass
        self.assertEqual(budget._reserved_bytes, 0)
