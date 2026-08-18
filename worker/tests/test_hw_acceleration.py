import unittest
import json
from unittest.mock import patch

from worker.app.constants import H264_AMF, H264_QSV, H264_VAAPI
from worker.app.hw_detect import detect_hw_accel, map_codec_to_hw
import worker.app.hw_detect as hw_detect
from worker.app.startup_tests import test_encoder_init as run_encoder_init
from worker.app.ffmpeg_helpers import cpu_filter_chain, replace_bitrate_args
from worker.app.tasks import _persist_job_telemetry, _publish
from worker.app.qsv_filters import (
    hardware_input_pixel_format,
    hardware_profile_flags,
    qsv_hardware_decode_supported,
    qsv_input_filter,
    qsv_probe_size,
    qsv_scaled_dimensions,
    qsv_vpp_filter,
    source_color_metadata_args,
    vaapi_input_filter,
)


class TestHardwareMapping(unittest.TestCase):
    def test_encoder_telemetry_is_persisted_in_existing_job_record(self):
        class FakeRedis:
            def __init__(self):
                self.values = {"job:task-telemetry": json.dumps({"state": "running"})}

            def get(self, key):
                return self.values.get(key)

            def setex(self, key, _ttl, value):
                self.values[key] = value

        fake = FakeRedis()
        with patch("worker.app.tasks._redis", return_value=fake):
            _persist_job_telemetry(
                "task-telemetry",
                {
                    "requested_encoder": "hevc_qsv",
                    "resolved_encoder": "hevc_qsv",
                    "hardware_type": "intel_qsv",
                    "hardware_device": "/dev/dri/renderD128",
                },
            )
        saved = json.loads(fake.values["job:task-telemetry"])
        self.assertEqual(saved["resolved_encoder"], "hevc_qsv")
        self.assertEqual(saved["hardware_device"], "/dev/dri/renderD128")

    def test_amf_mapping_is_native_software_frame_path(self):
        encoder, flags, init_flags = map_codec_to_hw(H264_AMF, {})
        self.assertEqual(encoder, H264_AMF)
        self.assertIn("-pix_fmt", flags)
        self.assertEqual(init_flags, [])

    @patch.object(hw_detect.os, "name", "posix")
    def test_qsv_uses_vaapi_backend_and_selected_device(self):
        encoder, flags, init_flags = map_codec_to_hw(
            H264_QSV,
            {"vaapi_device": "/dev/dri/renderD129"},
        )
        self.assertEqual(encoder, H264_QSV)
        self.assertEqual(flags, [])
        self.assertIn("vaapi=va:/dev/dri/renderD129", init_flags)
        self.assertIn("qsv=hw@va", init_flags)
        self.assertEqual(init_flags[-2:], ["-filter_hw_device", "hw"])

    @patch.object(hw_detect.os, "name", "nt")
    @patch.dict("os.environ", {"QSV_DEVICE": "1"}, clear=False)
    def test_windows_qsv_uses_qsv_device_option(self):
        encoder, flags, init_flags = map_codec_to_hw(H264_QSV, {})
        self.assertEqual(encoder, H264_QSV)
        self.assertEqual(flags, [])
        self.assertEqual(init_flags, ["-qsv_device", "1"])

    def test_vaapi_mapping_uses_filter_device(self):
        encoder, flags, init_flags = map_codec_to_hw(
            H264_VAAPI,
            {"vaapi_device": "/dev/dri/renderD130"},
        )
        self.assertEqual(encoder, H264_VAAPI)
        self.assertEqual(flags, [])
        self.assertIn("vaapi=va:/dev/dri/renderD130", init_flags)
        self.assertEqual(init_flags[-2:], ["-filter_hw_device", "va"])

    @patch("worker.app.hw_detect._check_nvidia", return_value=False)
    @patch(
        "worker.app.hw_detect.get_vaapi_devices",
        return_value=[{"path": "/dev/dri/renderD128", "vendor": "intel"}],
    )
    @patch("worker.app.hw_detect._encoder_list", return_value="libx264\nlibx265\nlibsvtav1\n")
    @patch("worker.app.hw_detect._test_encoder_on_device")
    def test_intel_qsv_is_not_collapsed_to_cpu(self, probe, _encoders, _devices, _nvidia):
        probe.side_effect = lambda encoder, _device: encoder in {"h264_qsv", "h264_vaapi"}
        info = detect_hw_accel()
        self.assertEqual(info["type"], "intel_qsv")
        self.assertEqual(info["available_encoders"]["h264"], "h264_qsv")
        self.assertIn("intel_qsv", info["available_types"])
        self.assertIn("/dev/dri/renderD128", info["encoder_devices"].values())

    @patch("worker.app.hw_detect._check_nvidia", return_value=False)
    @patch(
        "worker.app.hw_detect.get_vaapi_devices",
        return_value=[{"path": "/dev/dri/renderD129", "vendor": "amd"}],
    )
    @patch("worker.app.hw_detect._encoder_list", return_value="libx264\nlibx265\nlibsvtav1\n")
    @patch("worker.app.hw_detect._test_encoder_on_device")
    def test_amd_uses_vaapi_without_attempting_qsv(self, probe, _encoders, _devices, _nvidia):
        probe.side_effect = lambda encoder, _device: encoder == "h264_vaapi"
        info = detect_hw_accel()
        self.assertEqual(info["type"], "amd_vaapi")
        self.assertEqual(info["available_encoders"]["h264"], "h264_vaapi")
        self.assertFalse(any(call.args[0].endswith("_qsv") for call in probe.call_args_list))

    @patch("worker.app.hw_detect._check_nvidia", return_value=True)
    @patch(
        "worker.app.hw_detect.get_vaapi_devices",
        return_value=[{"path": "/dev/dri/renderD128", "vendor": "nvidia"}],
    )
    @patch(
        "worker.app.hw_detect._encoder_list",
        return_value="h264_nvenc hevc_nvenc av1_nvenc h264_vaapi hevc_vaapi av1_vaapi libx264 libx265 libsvtav1",
    )
    @patch("worker.app.hw_detect._test_encoder_on_device", return_value=True)
    def test_nvidia_render_node_does_not_enter_vaapi_path(
        self, probe, _encoders, _devices, _nvidia
    ):
        info = detect_hw_accel()

        self.assertEqual(info["type"], "nvidia")
        self.assertEqual(info["vaapi_devices"], [])
        self.assertIsNone(info["vaapi_device"])
        self.assertFalse(any(call.args[0].endswith("_vaapi") for call in probe.call_args_list))

    @patch("worker.app.hw_detect._check_nvidia", return_value=False)
    @patch(
        "worker.app.hw_detect.get_vaapi_devices",
        return_value=[{"path": "/dev/dri/renderD131", "vendor": "intel"}],
    )
    @patch("worker.app.hw_detect._encoder_list", return_value="libx264\nlibx265\nlibsvtav1\n")
    @patch("worker.app.hw_detect._test_encoder_on_device", return_value=False)
    def test_failed_probe_retains_render_node_for_rerun(self, _probe, _encoders, _devices, _nvidia):
        info = detect_hw_accel()
        self.assertEqual(info["type"], "cpu")
        self.assertEqual(info["vaapi_device"], "/dev/dri/renderD131")

    @patch.object(hw_detect.os, "name", "nt")
    @patch("worker.app.hw_detect._check_nvidia", return_value=False)
    @patch("worker.app.hw_detect.get_vaapi_devices", return_value=[])
    @patch(
        "worker.app.hw_detect._encoder_list",
        return_value="h264_amf hevc_amf av1_amf libx264 libx265 libsvtav1",
    )
    @patch("worker.app.hw_detect._test_encoder_on_device")
    def test_windows_amf_is_runtime_probed(self, probe, _encoders, _devices, _nvidia):
        probe.side_effect = lambda encoder, _device: encoder.endswith("_amf")
        info = detect_hw_accel()
        self.assertEqual(info["type"], "amd_amf")
        self.assertEqual(info["available_encoders"]["h264"], "h264_amf")
        self.assertTrue(all(call.args[0].endswith("_amf") for call in probe.call_args_list[:3]))


class TestHardwareFallbackHelpers(unittest.TestCase):
    def test_linux_qsv_hardware_decode_is_limited_to_upright_h264_hevc(self):
        self.assertTrue(qsv_hardware_decode_supported("linux", "h264", "h264_qsv"))
        self.assertTrue(qsv_hardware_decode_supported("linux", "hevc", "hevc_qsv"))
        self.assertFalse(qsv_hardware_decode_supported("linux", "av1", "hevc_qsv"))
        self.assertFalse(qsv_hardware_decode_supported("linux", "h264", "hevc_vaapi"))
        self.assertFalse(qsv_hardware_decode_supported("linux", "h264", "h264_qsv", 90))
        self.assertFalse(qsv_hardware_decode_supported("win32", "h264", "h264_qsv"))

    def test_qsv_vpp_filter_and_even_aspect_dimensions(self):
        self.assertEqual(qsv_scaled_dimensions(3840, 2280, max_height=1080), (1818, 1080))
        self.assertEqual(qsv_scaled_dimensions(1920, 1080, max_width=3840), None)
        self.assertEqual(
            qsv_vpp_filter("p010le", 1818, 1080, 24),
            "vpp_qsv=w=1818:h=1080:framerate=24:format=p010le",
        )

    def test_windows_qsv_uses_internal_upload_for_real_surfaces(self):
        self.assertEqual(qsv_input_filter("win32"), "format=nv12")
        self.assertEqual(qsv_probe_size("win32"), "1080x1920")

    def test_linux_qsv_keeps_fixed_hardware_frame_pool(self):
        self.assertEqual(
            qsv_input_filter("linux"),
            "format=nv12,hwupload=extra_hw_frames=64",
        )
        self.assertEqual(qsv_probe_size("linux"), "256x256")

    def test_10bit_hevc_hardware_uses_p010_main10(self):
        source = {
            "video_pix_fmt": "yuv420p10le",
            "video_profile": "Main",
            "video_color_space": "bt2020nc",
            "video_color_transfer": "smpte2084",
            "video_color_primaries": "bt2020",
            "video_color_range": "tv",
        }
        self.assertEqual(hardware_input_pixel_format("hevc_qsv", source), "p010le")
        self.assertEqual(hardware_input_pixel_format("hevc_vaapi", source), "p010le")
        self.assertEqual(hardware_profile_flags("hevc_qsv", "p010le"), ["-profile:v", "main10"])
        self.assertEqual(hardware_profile_flags("hevc_vaapi", "p010le"), ["-profile:v", "main10"])
        self.assertEqual(qsv_input_filter("linux", "p010le"), "format=p010le,hwupload=extra_hw_frames=64")
        self.assertEqual(vaapi_input_filter("p010le"), "format=p010le|vaapi,hwupload")
        self.assertEqual(source_color_metadata_args(source), [
            "-color_range", "tv",
            "-colorspace", "bt2020nc",
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
        ])

    def test_8bit_and_h264_remain_nv12(self):
        source = {"video_pix_fmt": "yuv420p"}
        self.assertEqual(hardware_input_pixel_format("hevc_qsv", source), "nv12")
        self.assertEqual(hardware_input_pixel_format("hevc_vaapi", source), "nv12")
        ten_bit = {"video_pix_fmt": "yuv420p10le"}
        self.assertEqual(hardware_input_pixel_format("h264_qsv", ten_bit), "nv12")
        self.assertEqual(hardware_input_pixel_format("h264_vaapi", ten_bit), "nv12")

    @patch("worker.app.tasks._redis")
    def test_progress_publish_failure_does_not_raise(self, redis_factory):
        redis_factory.return_value.publish.side_effect = ConnectionError("redis unavailable")
        self.assertFalse(_publish("task-progress", {"type": "log", "message": "test"}))

    def test_cpu_filter_chain_removes_embedded_hw_filters(self):
        filters = cpu_filter_chain([
            "scale_npp=1280:-2,hwdownload,format=yuv420p",
            "fps=30,format=nv12,hwupload=extra_hw_frames=64",
        ])
        self.assertEqual(filters, ["scale=1280:-2,format=yuv420p,fps=30"])

    def test_cpu_filter_chain_removes_p010_hardware_format(self):
        self.assertEqual(cpu_filter_chain(["format=p010le,hwupload=extra_hw_frames=64"]), [])

    def test_bitrate_retry_uses_all_vbv_arguments(self):
        command = ["ffmpeg", "-b:v", "2000k", "-maxrate", "2400k", "-bufsize", "4000k", "out.mp4"]
        updated = replace_bitrate_args(command, 1200)
        self.assertEqual(updated[1:7], ["-b:v", "1200k", "-maxrate", "1440k", "-bufsize", "2400k"])


class TestStartupProbeCommand(unittest.TestCase):
    @patch("worker.app.startup_tests.sys.platform", "win32")
    @patch("worker.app.startup_tests.subprocess.run")
    def test_windows_qsv_probe_matches_vertical_internal_upload_path(self, run):
        class Result:
            returncode = 0
            stderr = ""

        run.return_value = Result()
        ok, _message = run_encoder_init(
            "av1_qsv",
            ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"],
        )
        self.assertTrue(ok)
        command = run.call_args.args[0]
        self.assertIn("color=black:s=1080x1920:d=0.1:r=1", command)
        self.assertIn("format=nv12", command)
        self.assertNotIn("hwupload", " ".join(command))

    @patch("worker.app.startup_tests.sys.platform", "linux")
    @patch("worker.app.startup_tests.subprocess.run")
    def test_qsv_startup_probe_contains_correct_device_init(self, run):
        class Result:
            returncode = 0
            stderr = ""

        run.return_value = Result()
        ok, _message = run_encoder_init(
            "h264_qsv",
            [
                "-init_hw_device", "vaapi=va:/dev/dri/renderD128",
                "-init_hw_device", "qsv=hw@va",
                "-filter_hw_device", "hw",
            ],
        )
        self.assertTrue(ok)
        command = run.call_args.args[0]
        self.assertIn("vaapi=va:/dev/dri/renderD128", command)
        self.assertIn("qsv=hw@va", command)
        self.assertIn("format=nv12,hwupload=extra_hw_frames=64", command)

    @patch("worker.app.startup_tests.subprocess.run")
    def test_vaapi_startup_probe_allows_vaapi_frames(self, run):
        class Result:
            returncode = 0
            stderr = ""

        run.return_value = Result()
        ok, _message = run_encoder_init(
            "h264_vaapi",
            [
                "-init_hw_device", "vaapi=va:/dev/dri/renderD129",
                "-filter_hw_device", "va",
            ],
        )
        self.assertTrue(ok)
        command = run.call_args.args[0]
        self.assertIn("vaapi=va:/dev/dri/renderD129", command)
        self.assertIn("format=nv12|vaapi,hwupload", command)

    @patch("worker.app.startup_tests.subprocess.run")
    def test_amf_startup_probe_uses_yuv420p(self, run):
        class Result:
            returncode = 0
            stderr = ""

        run.return_value = Result()
        ok, _message = run_encoder_init("h264_amf", [])
        self.assertTrue(ok)
        command = run.call_args.args[0]
        self.assertIn("h264_amf", command)
        self.assertIn("yuv420p", command)


if __name__ == "__main__":
    unittest.main()
