from worker.app.tasks import _history_filename


def test_history_filename_strips_only_the_current_job_prefix():
    job_id = "41bb54b2-2342-4b22-a5a2-11b3aa9e901f"
    assert _history_filename(job_id, f"C:/uploads/{job_id}_vacation video.mp4") == "vacation video.mp4"


def test_history_filename_preserves_unrelated_names():
    assert _history_filename("current-job", "C:/uploads/another-job_video.mp4") == "another-job_video.mp4"
