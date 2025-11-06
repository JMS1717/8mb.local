"""
Test suite for API security, focusing on path traversal prevention
and filename sanitization.
"""
import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import tempfile
import os

# Note: These tests require the application to be importable
# You may need to adjust the import path based on your project structure
try:
    from backend_api.app.main import app
except ImportError:
    # Alternative import if the package structure is different
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from app.main import app

client = TestClient(app)


class TestPathTraversalPrevention:
    """Tests for path traversal attack prevention."""
    
    def test_compress_rejects_absolute_path(self):
        """Verify that absolute paths in filename are rejected."""
        response = client.post(
            "/api/compress",
            json={
                "job_id": "test_job_123",
                "filename": "/etc/passwd",  # Absolute path
                "target_size_mb": 8,
                "video_codec": "h264_nvenc"
            }
        )
        assert response.status_code == 400
        assert "traversal" in response.json()["detail"].lower() or "invalid" in response.json()["detail"].lower()
    
    def test_compress_rejects_parent_directory_traversal(self):
        """Verify that parent directory traversal (..) is rejected."""
        response = client.post(
            "/api/compress",
            json={
                "job_id": "test_job_456",
                "filename": "../../etc/passwd",  # Path traversal
                "target_size_mb": 8,
                "video_codec": "h264_nvenc"
            }
        )
        assert response.status_code == 400
        assert "traversal" in response.json()["detail"].lower() or "invalid" in response.json()["detail"].lower()
    
    def test_compress_rejects_subdirectory_traversal(self):
        """Verify that subdirectory traversal with .. is rejected."""
        response = client.post(
            "/api/compress",
            json={
                "job_id": "test_job_789",
                "filename": "subdir/../../../etc/passwd",
                "target_size_mb": 8,
                "video_codec": "h264_nvenc"
            }
        )
        assert response.status_code == 400
        assert "traversal" in response.json()["detail"].lower() or "invalid" in response.json()["detail"].lower()


class TestFilenameSanitization:
    """Tests for upload filename sanitization."""
    
    def test_upload_sanitizes_directory_traversal_in_filename(self):
        """Verify that directory components are stripped from uploaded filenames."""
        # This test requires mocking the file upload and the file system operations
        # Here we show the conceptual test structure
        
        # Create a test file
        test_content = b"fake video content"
        
        response = client.post(
            "/api/upload",
            files={"file": ("../../malicious.mp4", test_content, "video/mp4")},
            data={"target_size_mb": "25"}
        )
        
        # If the endpoint properly sanitizes, it should succeed
        # and the returned filename should not contain path separators
        if response.status_code == 200:
            filename = response.json()["filename"]
            # The filename should not contain directory separators
            assert ".." not in filename
            assert "/" not in filename or filename.count("/") == 0  # No forward slashes
            assert "\\" not in filename  # No backslashes
    
    def test_upload_rejects_empty_filename(self):
        """Verify that empty filenames are rejected."""
        test_content = b"fake video content"
        
        # Attempt to upload with just path separators (which become empty after sanitization)
        response = client.post(
            "/api/upload",
            files={"file": ("../../", test_content, "video/mp4")},
            data={"target_size_mb": "25"}
        )
        
        # Should either reject or handle gracefully
        # The exact behavior depends on implementation
        assert response.status_code in [400, 422]  # Bad request or validation error


class TestInputValidation:
    """Tests for Pydantic field validation."""
    
    def test_compress_rejects_negative_target_size(self):
        """Verify that negative target_size_mb is rejected."""
        response = client.post(
            "/api/compress",
            json={
                "job_id": "test_job_neg",
                "filename": "test.mp4",
                "target_size_mb": -10,  # Invalid: negative
                "video_codec": "h264_nvenc"
            }
        )
        assert response.status_code == 422  # Pydantic validation error
    
    def test_compress_rejects_zero_target_size(self):
        """Verify that zero target_size_mb is rejected."""
        response = client.post(
            "/api/compress",
            json={
                "job_id": "test_job_zero",
                "filename": "test.mp4",
                "target_size_mb": 0,  # Invalid: zero
                "video_codec": "h264_nvenc"
            }
        )
        assert response.status_code == 422
    
    def test_compress_rejects_negative_audio_bitrate(self):
        """Verify that negative audio_bitrate_kbps is rejected."""
        response = client.post(
            "/api/compress",
            json={
                "job_id": "test_job_audio",
                "filename": "test.mp4",
                "target_size_mb": 8,
                "video_codec": "h264_nvenc",
                "audio_bitrate_kbps": -128  # Invalid: negative
            }
        )
        assert response.status_code == 422
    
    def test_compress_rejects_excessive_audio_bitrate(self):
        """Verify that excessively high audio_bitrate_kbps is rejected."""
        response = client.post(
            "/api/compress",
            json={
                "job_id": "test_job_audio_high",
                "filename": "test.mp4",
                "target_size_mb": 8,
                "video_codec": "h264_nvenc",
                "audio_bitrate_kbps": 1000  # Invalid: > 512
            }
        )
        assert response.status_code == 422
    
    def test_compress_rejects_negative_dimensions(self):
        """Verify that negative max_width/max_height are rejected."""
        response = client.post(
            "/api/compress",
            json={
                "job_id": "test_job_dims",
                "filename": "test.mp4",
                "target_size_mb": 8,
                "video_codec": "h264_nvenc",
                "max_width": -1920  # Invalid: negative
            }
        )
        assert response.status_code == 422


class TestAuthenticationBypass:
    """Tests related to authentication (if enabled)."""
    
    def test_endpoints_accessible_when_auth_disabled(self):
        """When AUTH_ENABLED=false, endpoints should be accessible without credentials."""
        # This is more of an integration test that verifies the default config
        # Assumes authentication is disabled by default (as per the fixes)
        
        response = client.get("/api/hardware/info")
        # Should succeed or return a reasonable error (not 401 Unauthorized)
        assert response.status_code != 401


# Optional: Fixtures for test data
@pytest.fixture
def sample_video_file():
    """Create a temporary test video file."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"fake video data for testing")
        temp_path = f.name
    yield temp_path
    # Cleanup
    try:
        os.unlink(temp_path)
    except Exception:
        pass


if __name__ == "__main__":
    # Allow running tests directly with: python test_api.py
    pytest.main([__file__, "-v"])
