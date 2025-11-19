import unittest
from src.redirx.stages import HtmlPruneStage

class TestHtmlPruneStage(unittest.TestCase):
    def test_empty_input(self):
        stage = HtmlPruneStage()
        input = ([], [])
        expected_output = ([], [], set())
        actual_output = stage.execute(input)
        self.assertEqual(expected_output, actual_output)