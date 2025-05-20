import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Add src directory to sys.path to allow importing coderank_git
# This assumes the test script is run from the repo root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from coderank_git import get_git_file_history

class TestGetGitFileHistory(unittest.TestCase):
    @patch('coderank_git.subprocess.run')
    def test_get_git_history_success(self, mock_run):
        # Configure the mock for multiple calls
        mock_log_result = MagicMock()
        mock_log_result.stdout = '1678886400' # Sample timestamp
        mock_log_result.returncode = 0
        mock_log_result.stderr = ''

        mock_count_result = MagicMock()
        mock_count_result.stdout = '42' # Sample commit count
        mock_count_result.returncode = 0
        mock_count_result.stderr = ''

        # subprocess.run will be called twice. 
        # First for 'git log', second for 'git rev-list'
        mock_run.side_effect = [mock_log_result, mock_count_result]

        # Use a relative path for the file as it's made relative inside the function
        # The repo_abs_path is where 'git' commands are run
        expected_history = {'last_commit_timestamp': 1678886400, 'commit_count': 42}
        actual_history = get_git_file_history("/fake/repo/dummy/file.py", "/fake/repo")
        
        self.assertEqual(actual_history, expected_history)
        
        # Check calls to subprocess.run
        self.assertEqual(mock_run.call_count, 2)
        
        # Check first call (git log)
        args1, kwargs1 = mock_run.call_args_list[0]
        # Expected command for git log: ['git', 'log', '-1', '--format=%ct', '--', 'dummy/file.py']
        self.assertEqual(args1[0][0:4], ['git', 'log', '-1', '--format=%ct'])
        self.assertEqual(args1[0][-1], 'dummy/file.py') # Check relative path passed to git
        self.assertEqual(kwargs1['cwd'], '/fake/repo')

        # Check second call (git rev-list)
        args2, kwargs2 = mock_run.call_args_list[1]
        # Expected command for git rev-list: ['git', 'rev-list', '--count', 'HEAD', '--', 'dummy/file.py']
        self.assertEqual(args2[0][0:4], ['git', 'rev-list', '--count', 'HEAD'])
        self.assertEqual(args2[0][-1], 'dummy/file.py') # Check relative path
        self.assertEqual(kwargs2['cwd'], '/fake/repo')

    @patch('coderank_git.subprocess.run')
    def test_get_git_history_git_error(self, mock_run):
        # Simulate a git command failing
        mock_error_result = MagicMock()
        mock_error_result.returncode = 128
        mock_error_result.stdout = ''
        mock_error_result.stderr = 'git error'
        
        mock_run.side_effect = [mock_error_result, mock_error_result] # Both calls fail

        expected_history = {'last_commit_timestamp': 0, 'commit_count': 0}
        actual_history = get_git_file_history("/fake/repo/dummy/file.py", "/fake/repo")
        
        self.assertEqual(actual_history, expected_history)
        self.assertEqual(mock_run.call_count, 2) # Ensure it tried both

    @patch('coderank_git.subprocess.run')
    def test_get_git_history_empty_output(self, mock_run):
        # Simulate git commands returning empty stdout but successful return code
        mock_empty_log_result = MagicMock()
        mock_empty_log_result.stdout = ''
        mock_empty_log_result.returncode = 0
        mock_empty_log_result.stderr = ''

        mock_empty_count_result = MagicMock()
        mock_empty_count_result.stdout = '   ' # Test with whitespace
        mock_empty_count_result.returncode = 0
        mock_empty_count_result.stderr = ''
        
        mock_run.side_effect = [mock_empty_log_result, mock_empty_count_result]

        expected_history = {'last_commit_timestamp': 0, 'commit_count': 0}
        actual_history = get_git_file_history("/fake/repo/dummy/file.py", "/fake/repo")
        
        self.assertEqual(actual_history, expected_history)
        self.assertEqual(mock_run.call_count, 2)

    @patch('coderank_git.subprocess.run')
    def test_get_git_history_file_not_found_error(self, mock_run):
        # Simulate FileNotFoundError (e.g., git not installed)
        mock_run.side_effect = FileNotFoundError("git command not found")

        expected_history = {'last_commit_timestamp': 0, 'commit_count': 0}
        actual_history = get_git_file_history("/fake/repo/dummy/file.py", "/fake/repo")
        
        self.assertEqual(actual_history, expected_history)
        # subprocess.run would be called once and raise FileNotFoundError
        self.assertEqual(mock_run.call_count, 1)

if __name__ == '__main__':
    unittest.main()
