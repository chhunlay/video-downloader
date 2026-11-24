"""Tests for the small helpers in telegram_bot.py, and its download counting."""

import asyncio
import os
from types import SimpleNamespace

import pytest
from telegram.error import TimedOut

import stats
import telegram_bot as bot


@pytest.mark.parametrize("url, expected", [
    ("https://www.tiktok.com/@u/video/1", "tiktok"),
    ("https://youtu.be/abc", "video"),
    ("https://www.instagram.com/reel/abc", "video"),
])
def test_pick_dtype(url, expected):
    """
    Checks TikTok links get the no-watermark type and others get video.

    Parameters:
    url (str): The link.
    expected (str): The expected download type.
    """
    assert bot.pick_dtype(url) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://www.YouTube.com/watch?v=abc", True),
    ("https://youtu.be/abc", True),
    ("https://www.tiktok.com/@u/video/1", False),
])
def test_is_youtube(url, expected):
    """
    Checks YouTube links are recognized, including short links.

    Parameters:
    url (str): The link.
    expected (bool): The expected result.
    """
    assert bot.is_youtube(url) is expected


def test_with_size():
    """Checks the size is appended only when known."""
    assert bot.with_size("720p", "12.3MB") == "720p (12.3MB)"
    assert bot.with_size("720p", None) == "720p"


@pytest.mark.parametrize("percent, filled", [(0, 0), (50, 6), (100, 12)])
def test_render_bar(percent, filled):
    """
    Checks the progress bar is always full length and fills proportionally.

    Parameters:
    percent (int): 0-100.
    filled (int): Expected number of filled cells.
    """
    bar = bot.render_bar(percent)
    assert len(bar) == bot.PROGRESS_BAR_LEN
    assert bar.count("▓") == filled


def test_progress_text():
    """Checks the progress message includes label, bar and percent."""
    assert bot.progress_text(bot.DOWNLOADING, 50) == f"{bot.DOWNLOADING}  {bot.render_bar(50)}  50%"


# ---------- Download counts ----------

class FakeMessage:
    """Stands in for the bot's progress message."""

    async def edit_text(self, *args, **kwargs):
        """Ignores progress text."""

    async def delete(self):
        """Ignores the delete."""


class FakeBot:
    """Stands in for context.bot; send_video fails when told to."""

    def __init__(self, send_error=None):
        """
        Stores the error send_video should raise.

        Parameters:
        send_error (Exception/None): Raised by every send_video call.
        """
        self.send_error = send_error

    async def send_chat_action(self, **kwargs):
        """Ignores the typing indicator."""

    async def send_video(self, **kwargs):
        """Fails if told to, else accepts the video."""
        if self.send_error:
            raise self.send_error


def run_bot_download(monkeypatch, tmp_path, send_error=None, download_error=None):
    """
    Runs run_download_and_send with fake Telegram objects and a fake download.

    Parameters:
    monkeypatch (pytest.MonkeyPatch): pytest's patcher.
    tmp_path (pathlib.Path): Holds the stats file.
    send_error (Exception/None): Makes the upload fail.
    download_error (Exception/None): Makes the download fail.

    Returns:
    dict: stats.summary() of the temp stats file afterwards.
    """
    stats_file = str(tmp_path / "stats.jsonl")
    monkeypatch.setattr(bot, "STATS_FILE", stats_file)
    monkeypatch.setattr(bot, "probe_dimensions", lambda path: (1280, 720, 10))

    def fake_download(url, dtype, out_dir, **kwargs):
        if download_error:
            raise download_error
        path = os.path.join(out_dir, "clip.mp4")
        with open(path, "wb") as f:
            f.write(b"12345")
        return path

    monkeypatch.setattr(bot, "download_media", fake_download)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=1))
    context = SimpleNamespace(bot=FakeBot(send_error))
    asyncio.run(bot.run_download_and_send(
        update, context, FakeMessage(), "https://youtu.be/dQw4w9WgXcQ", "video", None,
    ))
    return stats.summary(stats_file)


def test_bot_download_is_counted(monkeypatch, tmp_path):
    """Checks a video the bot sent is counted with its site and size."""
    summary = run_bot_download(monkeypatch, tmp_path)
    assert summary["total"] == 1
    assert summary["top_site"] == "YouTube"
    assert summary["total_bytes"] == 5


def test_bot_failed_download_not_counted(monkeypatch, tmp_path):
    """Checks a failed bot download is not counted."""
    summary = run_bot_download(monkeypatch, tmp_path, download_error=Exception("Video unavailable"))
    assert summary["total"] == 0


def test_bot_failed_upload_not_counted(monkeypatch, tmp_path):
    """Checks a video that never reached the chat is not counted."""
    summary = run_bot_download(monkeypatch, tmp_path, send_error=TimedOut())
    assert summary["total"] == 0
