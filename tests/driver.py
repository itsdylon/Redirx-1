import os
import sys

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(parent_dir)

import unittest
import stage_tests.html_prune_test

if __name__ == '__main__':
    unittest.main()