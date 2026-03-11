import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.reddit_benchmark import run as rb


class TestContentRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo_dir = Path(self.tmp.name)
        self.context = rb.RecoverContext(
            source_config={"site_base_url": "https://example.com", "source_id": "test"},
            repo_dir=self.repo_dir,
            repo_commit="headsha",
            pre_commit_by_redirect_file={"redirects.txt": "presha"},
            migration_date_by_redirect_file={"redirects.txt": "2025-01-01T00:00:00+00:00"},
        )

    @patch.object(rb, "write_pair_content_text")
    @patch.object(rb, "fetch_live_html")
    @patch.object(rb, "wayback_snapshot_for_url")
    @patch.object(rb, "get_route_index")
    def test_wayback_fallback_when_git_missing(self, mock_get_index, mock_wayback, mock_fetch, mock_write):
        pre_index = MagicMock()
        pre_index.best_file_for_path.return_value = None

        head_index = MagicMock()
        head_index.best_file_for_path.return_value = None

        def index_side_effect(repo_dir, ref):
            return pre_index if ref == "presha" else head_index

        mock_get_index.side_effect = index_side_effect
        mock_wayback.return_value = ("20240101000000", "https://web.archive.org/web/20240101000000id_/https://example.com/old")
        varied_old = " ".join([f"token{i}" for i in range(200)])
        mock_fetch.side_effect = [f"<html><body>{varied_old}</body></html>", None]

        row = {
            "pair_id": "pair1",
            "old_url_path": "/old",
            "new_url_path": "/new",
            "redirect_file": "redirects.txt",
        }

        out = rb.recover_pair_content(row, self.context)
        self.assertEqual(out["old_content_source"], "wayback")
        self.assertEqual(out["old_content_quality_pass"], "true")

    @patch.object(rb, "write_pair_content_text")
    @patch.object(rb, "fetch_live_html")
    @patch.object(rb, "get_route_index")
    def test_git_history_preferred(self, mock_get_index, mock_fetch, mock_write):
        pre_index = MagicMock()
        pre_index.best_file_for_path.return_value = "docs/old.md"
        pre_index.text_for_file.return_value = " ".join([f"historical{i}" for i in range(200)])

        head_index = MagicMock()
        head_index.best_file_for_path.return_value = "docs/new.md"
        head_index.text_for_file.return_value = " ".join([f"new{i}" for i in range(200)])

        def index_side_effect(repo_dir, ref):
            return pre_index if ref == "presha" else head_index

        mock_get_index.side_effect = index_side_effect
        mock_fetch.return_value = None

        row = {
            "pair_id": "pair2",
            "old_url_path": "/old",
            "new_url_path": "/new",
            "redirect_file": "redirects.txt",
        }

        out = rb.recover_pair_content(row, self.context)
        self.assertEqual(out["old_content_source"], "git_history")
        self.assertEqual(out["new_content_source"], "repo_head")


if __name__ == "__main__":
    unittest.main()
