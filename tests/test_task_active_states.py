from core.task import Task, TaskState


def test_qc_passed_task_is_still_active():
    task = Task(state=TaskState.QC_PASSED.value)

    assert task.is_active is True
