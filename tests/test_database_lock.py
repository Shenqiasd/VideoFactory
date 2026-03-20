"""Tests for Database thread safety and UPSERT operations."""
import threading
import pytest
from core.database import Database


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    return Database(db_path=db_path)


class TestDatabaseThreadSafety:
    def test_concurrent_writes_no_corruption(self, db):
        """Multiple threads writing simultaneously should not corrupt data."""
        errors = []

        def insert_account(i):
            try:
                db.insert_account({
                    "id": f"acc-{i}",
                    "platform": "bilibili",
                    "name": f"Account {i}",
                    "cookie_path": f"/tmp/cookie-{i}",
                    "status": "active",
                    "created_at": "2026-01-01T00:00:00",
                })
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=insert_account, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent write errors: {errors}"
        accounts = db.get_accounts()
        assert len(accounts) == 20

    def test_reentrant_lock_set_default_account(self, db):
        """set_default_account calls get_account internally - must not deadlock."""
        db.insert_account({
            "id": "acc-1",
            "platform": "bilibili",
            "name": "Account 1",
            "cookie_path": "/tmp/cookie",
            "status": "active",
            "created_at": "2026-01-01T00:00:00",
        })
        result = db.set_default_account("acc-1")
        assert result is True


class TestDatabaseUpsert:
    def _make_job(self, job_id="job-1", status="pending"):
        return {
            "job_id": job_id,
            "task_id": "task-1",
            "platform": "bilibili",
            "scheduled_time": 1000.0,
            "product": {"path": "/video.mp4"},
            "metadata": {},
            "product_type": "long_video",
            "product_identity": "vid-1",
            "idempotency_key": f"key-{job_id}",
            "status": status,
            "result": {},
            "retry_count": 0,
            "max_retries": 3,
        }

    def test_upsert_creates_new_job(self, db):
        db.upsert_publish_job(self._make_job())
        jobs = db.get_publish_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "job-1"

    def test_upsert_updates_existing_job(self, db):
        db.upsert_publish_job(self._make_job(status="pending"))
        db.upsert_publish_job(self._make_job(status="done"))
        jobs = db.get_publish_jobs()
        assert len(jobs) == 1
        assert jobs[0]["status"] == "done"

    def test_delete_publish_job(self, db):
        db.upsert_publish_job(self._make_job())
        db.delete_publish_job("job-1")
        jobs = db.get_publish_jobs()
        assert len(jobs) == 0

    def test_update_publish_job_status(self, db):
        db.upsert_publish_job(self._make_job())
        db.update_publish_job_status("job-1", "done", {"url": "https://example.com"})
        jobs = db.get_publish_jobs()
        assert jobs[0]["status"] == "done"
        assert jobs[0]["result"]["url"] == "https://example.com"

    def test_concurrent_upserts(self, db):
        """Multiple threads upserting different jobs should not corrupt data."""
        errors = []

        def upsert_job(i):
            try:
                db.upsert_publish_job(self._make_job(job_id=f"job-{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=upsert_job, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        jobs = db.get_publish_jobs()
        assert len(jobs) == 20
