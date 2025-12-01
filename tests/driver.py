import os
import sys

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

import unittest

# Import all test modules
import stage_tests.html_prune_test
import stage_tests.test_url_prune_stage
import stage_tests.test_embed_stage
import stage_tests.test_pairing_stage
import stage_tests.test_text_extraction
import integration_tests.test_embed_pairing_integration
import integration_tests.test_full_pipeline_e2e

if __name__ == '__main__':
    unittest.main()