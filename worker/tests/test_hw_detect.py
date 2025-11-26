import unittest
from unittest.mock import patch, MagicMock
import subprocess

# Import the module under test
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "hw_detect",
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app', 'hw_detect.py'))
)
hd = importlib.util.module_from_spec(_spec)

# Load module with mocked subprocess to avoid side effects during import
with patch('subprocess.run') as _mock_run, patch('glob.glob', return_value=[]):
    _mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='')
    _spec.loader.exec_module(hd)


class TestHwDetect(unittest.TestCase):
    def setUp(self):
        # Clear module cache before each test
        hd._HW_CACHE = None
        hd._HW_INFO = None

    @patch('glob.glob')
    @patch.object(subprocess, 'run')
    def test_detect_nvidia(self, mock_run, mock_glob):
        mock_glob.return_value = []
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

    @patch('glob.glob')
    @patch.object(subprocess, 'run')
    def test_detect_cpu_fallback(self, mock_run, mock_glob):
        mock_glob.return_value = []
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

    @patch('glob.glob')
    @patch.object(subprocess, 'run')
    def test_detect_amd_not_intel_qsv(self, mock_run, mock_glob):
        """Test that AMD systems with QSV encoders listed are detected as AMD, not Intel.
        
        This tests the fix for: https://github.com/JMS1717/8mb.local/issues/X
        On AMD Ryzen systems with integrated Radeon graphics, FFmpeg may list QSV
        encoders even though there's no Intel hardware. The system should correctly
        detect AMD/VAAPI instead of incorrectly detecting Intel/QSV.
        """
        mock_glob.return_value = ['/dev/dri/renderD128']
        
        def run_side_effect(args, **kwargs):
            class R:
                def __init__(self, returncode=0, stdout='', stderr=''):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = stderr
            
            if isinstance(args, list) and args:
                if args[0] == 'nvidia-smi':
                    raise FileNotFoundError("nvidia-smi not found")
                if args[0] == 'vainfo':
                    # AMD Radeon vainfo output
                    return R(returncode=0, stdout='vainfo: Driver version: Mesa Gallium driver for AMD Radeon 780M (radeonsi)')
                if args[0] == 'lspci':
                    # AMD GPU only, no Intel
                    return R(returncode=0, stdout='00:02.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Phoenix1')
                if args[0] == 'ffmpeg':
                    if '-hwaccels' in args:
                        # QSV and VAAPI both reported as available
                        return R(returncode=0, stdout='Hardware acceleration methods:\nvaapi\nqsv\n')
                    if '-encoders' in args:
                        # Both QSV and VAAPI encoders listed (QSV won't work on AMD)
                        return R(returncode=0, stdout='''Encoders:
 V....D h264_vaapi           H.264/AVC (VAAPI)
 V....D hevc_vaapi           H.265/HEVC (VAAPI)
 V....D av1_vaapi            AV1 (VAAPI)
 V..... h264_qsv             H.264 (Intel Quick Sync Video)
 V..... hevc_qsv             HEVC (Intel Quick Sync Video)
''')
            return R(returncode=1)
        
        mock_run.side_effect = run_side_effect
        info = hd.detect_hw_accel()
        
        # Should detect AMD with VAAPI, NOT Intel with QSV
        self.assertEqual(info['type'], 'amd', "AMD system should be detected as 'amd', not 'intel'")
        self.assertEqual(info['decode_method'], 'vaapi', "AMD system should use 'vaapi' decode method")
        self.assertEqual(info['available_encoders'].get('h264'), 'h264_vaapi', "Should use VAAPI encoder for h264")
        self.assertEqual(info['available_encoders'].get('hevc'), 'hevc_vaapi', "Should use VAAPI encoder for hevc")

    @patch('glob.glob')
    @patch.object(subprocess, 'run')
    def test_detect_intel_qsv(self, mock_run, mock_glob):
        """Test that Intel systems are correctly detected with QSV support."""
        mock_glob.return_value = ['/dev/dri/renderD128']
        
        def run_side_effect(args, **kwargs):
            class R:
                def __init__(self, returncode=0, stdout='', stderr=''):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = stderr
            
            if isinstance(args, list) and args:
                if args[0] == 'nvidia-smi':
                    raise FileNotFoundError("nvidia-smi not found")
                if args[0] == 'vainfo':
                    # Intel vainfo output
                    return R(returncode=0, stdout='vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics')
                if args[0] == 'lspci':
                    return R(returncode=0, stdout='00:02.0 VGA compatible controller: Intel Corporation UHD Graphics 620')
                if args[0] == 'ffmpeg':
                    if '-hwaccels' in args:
                        return R(returncode=0, stdout='Hardware acceleration methods:\nvaapi\nqsv\n')
                    if '-encoders' in args:
                        return R(returncode=0, stdout='''Encoders:
 V..... h264_qsv             H.264 (Intel Quick Sync Video)
 V..... hevc_qsv             HEVC (Intel Quick Sync Video)
 V....D h264_vaapi           H.264/AVC (VAAPI)
''')
            return R(returncode=1)
        
        mock_run.side_effect = run_side_effect
        info = hd.detect_hw_accel()
        
        # Should detect Intel with QSV
        self.assertEqual(info['type'], 'intel', "Intel system should be detected as 'intel'")
        self.assertEqual(info['decode_method'], 'qsv', "Intel system should use 'qsv' decode method")
        self.assertEqual(info['available_encoders'].get('h264'), 'h264_qsv', "Should use QSV encoder for h264")


if __name__ == '__main__':
    unittest.main()
