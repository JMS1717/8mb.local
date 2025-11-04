import unittest
from unittest.mock import patch

# Import the module under test
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

import hw_detect as hd

class TestHwDetect(unittest.TestCase):
    @patch('subprocess.run')
    def test_detect_nvidia(self, mock_run):
        # Mock nvidia-smi success
        def run_side_effect(args, **kwargs):
            class R:
                def __init__(self, returncode=0, stdout=''):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = ''
            if isinstance(args, list) and args and args[0] == 'nvidia-smi':
                return R(returncode=0)
            if isinstance(args, list) and args and args[0] == 'ffmpeg':
                if '-hwaccels' in args:
                    return R(returncode=0, stdout='Hardware acceleration methods:\ncuda\nvaapi\n')
                if '-encoders' in args:
                    return R(returncode=0, stdout='Encoders:\nh264_nvenc\nhevc_nvenc\nav1_nvenc\n')
            return R(returncode=1)
        mock_run.side_effect = run_side_effect
        info = hd.detect_hw_accel()
        self.assertEqual(info['type'], 'nvidia')
        self.assertEqual(info['available_encoders'].get('hevc'), 'hevc_nvenc')

    @patch('subprocess.run')
    def test_detect_cpu_fallback(self, mock_run):
        # Everything fails -> CPU
        def run_side_effect(args, **kwargs):
            class R:
                def __init__(self):
                    self.returncode = 1
                    self.stdout = ''
                    self.stderr = ''
            return R()
        mock_run.side_effect = run_side_effect
        info = hd.detect_hw_accel()
        self.assertEqual(info['type'], 'cpu')
        self.assertEqual(info['available_encoders'].get('av1'), 'libaom-av1')

if __name__ == '__main__':
    unittest.main()
