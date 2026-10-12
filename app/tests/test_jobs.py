"""Tests for the job store in jobs.py (temp folders only)."""

import json
import os
import time

from jobs import JobStore, is_valid_job_id


def test_create_makes_folder_and_json(tmp_path):
    """Checks a new job gets its own folder and a job.json."""
    store = JobStore(str(tmp_path))
    job = store.create("single", "video", {"resolution": "720"})
    assert is_valid_job_id(job.id)
    folder = tmp_path / job.id
    assert folder.is_dir()
    saved = json.loads((folder / "job.json").read_text())
    assert saved["status"] == "queued"
    assert saved["options"] == {"resolution": "720"}


def test_get_rejects_bad_ids(tmp_path):
    """Checks ids that aren't 32 hex chars never reach the filesystem."""
    store = JobStore(str(tmp_path))
    assert store.get("../etc") is None
    assert store.get("abc") is None
    assert not is_valid_job_id("../" + "a" * 29)


def test_update_persists(tmp_path):
    """Checks updates are written to disk and survive a reload."""
    store = JobStore(str(tmp_path))
    job = store.create("single", "video", {})
    store.update(job.id, status="done", files=["a.mp4"])

    reloaded = JobStore(str(tmp_path))
    reloaded.load()
    again = reloaded.get(job.id)
    assert again.status == "done"
    assert again.files == ["a.mp4"]


def test_delete_removes_folder(tmp_path):
    """Checks deleting a job removes its folder, and a second delete reports False."""
    store = JobStore(str(tmp_path))
    job = store.create("single", "audio", {})
    assert store.delete(job.id) is True
    assert not (tmp_path / job.id).exists()
    assert store.get(job.id) is None
    assert store.delete(job.id) is False


def test_load_marks_running_jobs_interrupted(tmp_path):
    """Checks a job that was mid-download when the server stopped shows as interrupted."""
    store = JobStore(str(tmp_path))
    job = store.create("single", "video", {})
    store.update(job.id, status="downloading", percent=40)

    reloaded = JobStore(str(tmp_path))
    reloaded.load()
    assert reloaded.get(job.id).status == "interrupted"


def test_load_drops_broken_folders(tmp_path):
    """Checks folders with a missing or unreadable job.json are deleted."""
    broken = tmp_path / ("a" * 32)
    broken.mkdir()
    (broken / "job.json").write_text("{not json")
    empty = tmp_path / ("b" * 32)
    empty.mkdir()

    store = JobStore(str(tmp_path))
    store.load()
    assert not broken.exists()
    assert not empty.exists()


def test_load_removes_old_loose_files(tmp_path):
    """Checks loose files from the old layout are deleted once older than 24 h."""
    old = tmp_path / "old.mp4"
    old.write_bytes(b"x")
    stamp = time.time() - 25 * 3600
    os.utime(old, (stamp, stamp))
    new = tmp_path / "new.mp4"
    new.write_bytes(b"x")

    JobStore(str(tmp_path)).load()
    assert not old.exists()
    assert new.exists()


def test_cleanup_removes_only_expired(tmp_path):
    """Checks cleanup deletes jobs older than 24 h and keeps newer ones."""
    store = JobStore(str(tmp_path))
    now = time.time()
    old = store.create("single", "video", {})
    store.update(old.id, created=now - 25 * 3600)
    fresh = store.create("single", "video", {})
    store.update(fresh.id, created=now - 3600)

    assert store.cleanup(now=now) == 1
    assert store.get(old.id) is None
    assert store.get(fresh.id) is not None
    assert not (tmp_path / old.id).exists()
