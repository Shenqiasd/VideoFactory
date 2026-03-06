from api.routes.pages import calculate_task_progress


def test_subtitle_only_processing_progress_is_mapped():
    progress = calculate_task_progress(
        {
            "task_scope": "subtitle_only",
            "state": "processing",
        }
    )
    assert progress == 85


def test_subtitle_only_ready_to_publish_progress_is_mapped():
    progress = calculate_task_progress(
        {
            "task_scope": "subtitle_only",
            "state": "ready_to_publish",
        }
    )
    assert progress == 96
