import unittest
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from worker.app.hw_detect import detect_hw_accel

class TestHwDetect(unittest.TestCase):
    @patch('worker.app.hw_detect.test_encoder')
    @patch('subprocess.run')
    def test_detect_nvidia(self, mock_run, mock_test_encoder):
        mock_test_encoder.return_value = True
        def run_side_effect(args, **kwargs):
            class R:
                def __init__(self, returncode=0, stdout='', stderr=''):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = stderr
            if isinstance(args, list) and args and args[0] == 'nvidia-smi':
                return R(returncode=0, stdout='NVIDIA RTX 5070 Ti')
            if isinstance(args, list) and args and args[0] == 'ffmpeg':
                if '-hwaccels' in args:
                    return R(returncode=0, stdout='Hardware acceleration methods:\ncuda\n')
                if '-encoders' in args:
                    return R(returncode=0, stdout='Encoders:\nh264_nvenc\nhevc_nvenc\nav1_nvenc\n')
            return R(returncode=1)
        mock_run.side_effect = run_side_effect
        info = detect_hw_accel()
        self.assertEqual(info['type'], 'nvidia')
        self.assertIn('hevc', info['available_encoders'])

    @patch('subprocess.run')
    def test_detect_cpu_fallback(self, mock_run):
        def run_side_effect(args, **kwargs):
            class R:
                def __init__(self):
                    self.returncode = 1
                    self.stdout = ''
                    self.stderr = ''
            return R()
        mock_run.side_effect = run_side_effect
        info = detect_hw_accel()
        self.assertEqual(info['type'], 'cpu')
        self.assertEqual(info['available_encoders'].get('av1'), 'libaom-av1')

if __name__ == '__main__':
    unittest.main()
