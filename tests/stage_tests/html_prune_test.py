import unittest
import os
import sys

# Add project root to Python path
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, parent_dir)

from src.redirx.stages import HtmlPruneStage

class TestHtmlPruneStage(unittest.TestCase):
    def test_empty_input(self):
        stage = HtmlPruneStage()
        input = ([], [])
        expected_output = ([], [], set())
        actual_output = stage.execute(input)
        self.assertEqual(expected_output, actual_output)