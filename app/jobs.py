"""Per-download jobs for the web app: one folder and one job.json per job."""

import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

MAX_AGE_SECONDS = 24 * 3600
CLEANUP_INTERVAL_SECONDS = 600

# Statuses after which a job no longer changes
FINAL_STATUSES = ("done", "error", "interrupted")

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def is_valid_job_id(job_id):
    """
    Checks whether a string is a job id (32 lowercase hex chars).

    Parameters:
    job_id (str): The id to check.

    Returns:
    bool: True if it has the uuid4().hex shape.
    """
    return isinstance(job_id, str) and bool(_JOB_ID_RE.match(job_id))


@dataclass
class Job:
    """One download, single or batch, and its progress."""

    id: str
    kind: str
    dtype: str
    options: dict
    status: str = "queued"
    percent: int = 0
    # Live download details; None when unknown or not downloading
    downloaded_bytes: int = None
    total_bytes: int = None
    speed: int = None
    eta: int = None
    title: str = None
    files: list = field(default_factory=list)
    error: str = None
    item_index: int = 0
    item_count: int = 1
    failed_items: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    # Only set by 0.10.0, which moved local downloads to ~/Downloads; kept so
    # those jobs still load and serve their files
    saved_locally: bool = False

    def public(self):
        """
        Returns the fields the page is allowed to see.

        Returns:
        dict: Every field except options.
        """
        data = asdict(self)
        del data["options"]
        return data


class JobStore:
    """Thread-safe registry of jobs, persisted as root_dir/<id>/job.json."""

    def __init__(self, root_dir):
        """
        Creates the store; call load() to pick up jobs from a previous run.

        Parameters:
        root_dir (str): Folder holding one sub-folder per job.
        """
        self.root_dir = root_dir
        self._jobs = {}
        self._lock = threading.Lock()
        os.makedirs(root_dir, exist_ok=True)

    def folder(self, job_id):
        """
        Gets a job's folder path.

        Parameters:
        job_id (str): The job id.

        Returns:
        str: root_dir/<job_id>.
        """
        return os.path.join(self.root_dir, job_id)

    def _save(self, job):
        """
        Writes a job's job.json atomically (temp file, then swap).

        Parameters:
        job (Job): The job to save.
        """
        path = os.path.join(self.folder(job.id), "job.json")
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(asdict(job), f, ensure_ascii=False)
        os.replace(tmp_path, path)

    def create(self, kind, dtype, options):
        """
        Creates a job with its own folder.

        Parameters:
        kind (str): "single" or "batch".
        dtype (str): "video", "audio" or "tiktok".
        options (dict): Download options (resolution, audio_quality, trim, subtitles).

        Returns:
        Job: The new job, status "queued".
        """
        job = Job(id=uuid.uuid4().hex, kind=kind, dtype=dtype, options=options)
        with self._lock:
            os.makedirs(self.folder(job.id))
            self._jobs[job.id] = job
            self._save(job)
        return job

    def get(self, job_id):
        """
        Looks up a job.

        Parameters:
        job_id (str): The job id.

        Returns:
        Job/None: The job, or None for unknown or malformed ids.
        """
        if not is_valid_job_id(job_id):
            return None
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id, **fields):
        """
        Changes a job's fields and saves it.

        Parameters:
        job_id (str): The job id.
        **fields: Job attributes to set.

        Returns:
        Job/None: The updated job, or None if it doesn't exist.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for name, value in fields.items():
                setattr(job, name, value)
            # The folder can vanish if the job was deleted mid-download
            if os.path.isdir(self.folder(job_id)):
                self._save(job)
            return job

    def delete(self, job_id):
        """
        Deletes a job and its files.

        Parameters:
        job_id (str): The job id.

        Returns:
        bool: True if the job existed.
        """
        if not is_valid_job_id(job_id):
            return False
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
            shutil.rmtree(self.folder(job_id), ignore_errors=True)
        return existed

    def load(self):
        """
        Loads jobs saved by a previous run.

        Jobs that were still running become "interrupted". Folders without a
        readable job.json are deleted, and so are loose files (from the old
        one-folder layout) older than MAX_AGE_SECONDS.
        """
        now = time.time()
        with self._lock:
            for name in os.listdir(self.root_dir):
                path = os.path.join(self.root_dir, name)
                if os.path.isfile(path):
                    if now - os.path.getmtime(path) > MAX_AGE_SECONDS:
                        os.remove(path)
                    continue
                try:
                    with open(os.path.join(path, "job.json"), encoding="utf-8") as f:
                        job = Job(**json.load(f))
                except (OSError, ValueError, TypeError):
                    shutil.rmtree(path, ignore_errors=True)
                    continue
                if job.status not in FINAL_STATUSES:
                    job.status = "interrupted"
                    self._save(job)
                self._jobs[job.id] = job

    def cleanup(self, max_age=MAX_AGE_SECONDS, now=None):
        """
        Deletes jobs older than max_age.

        Parameters:
        max_age (int/float): Age limit in seconds.
        now (float/None): Current time; defaults to time.time().

        Returns:
        int: Number of jobs deleted.
        """
        now = time.time() if now is None else now
        with self._lock:
            expired = [j.id for j in self._jobs.values() if now - j.created > max_age]
        for job_id in expired:
            self.delete(job_id)
        return len(expired)

    def start_cleanup_thread(self, interval=CLEANUP_INTERVAL_SECONDS):
        """
        Starts a daemon thread that runs cleanup() every interval seconds.

        Parameters:
        interval (int/float): Seconds between cleanups.

        Returns:
        threading.Thread: The started thread.
        """
        def loop():
            while True:
                time.sleep(interval)
                try:
                    self.cleanup()
                except Exception as e:
                    print("Job cleanup failed:", e)

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return thread
