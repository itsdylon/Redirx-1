import unittest

from scripts.reddit_benchmark import run as rb


class TestMatchAndMetrics(unittest.TestCase):
    def test_top_k_predictions_sorted(self):
        scored = [
            (0.4, {"candidate_url_path": "/b"}),
            (0.9, {"candidate_url_path": "/a"}),
            (0.7, {"candidate_url_path": "/c"}),
        ]
        top = rb.top_k_predictions(scored, top_k=2)
        self.assertEqual(top[0][2]["candidate_url_path"], "/a")
        self.assertEqual(top[1][2]["candidate_url_path"], "/c")

    def test_evaluate_metrics_known_values(self):
        pairs = [
            {"pair_id": "p1", "source_repo": "r", "new_url_path": "/n1"},
            {"pair_id": "p2", "source_repo": "r", "new_url_path": "/n2"},
            {"pair_id": "p3", "source_repo": "r", "new_url_path": "/n3"},
        ]
        preds = [
            {"pair_id": "p1", "rank": "1", "candidate_url_path": "/n1", "runtime_ms": "10", "method": "string_similarity"},
            {"pair_id": "p2", "rank": "1", "candidate_url_path": "/x", "runtime_ms": "20", "method": "string_similarity"},
            {"pair_id": "p2", "rank": "2", "candidate_url_path": "/n2", "runtime_ms": "20", "method": "string_similarity"},
            {"pair_id": "p3", "rank": "1", "candidate_url_path": "/x", "runtime_ms": "30", "method": "string_similarity"},
        ]
        summary, by_source = rb.evaluate_predictions_for_method(
            "string_similarity",
            preds,
            pairs,
            snapshots_by_pair={},
            match_stats={},
        )

        self.assertAlmostEqual(float(summary["top1_accuracy"]), 1 / 3, places=6)
        self.assertAlmostEqual(float(summary["top3_recall"]), 2 / 3, places=6)
        # MRR = (1 + 1/2 + 0) / 3
        self.assertAlmostEqual(float(summary["mrr"]), 0.5, places=6)
        self.assertEqual(len(by_source), 1)


if __name__ == "__main__":
    unittest.main()
