"""Tests for the small helpers in telegram_bot.py."""

import pytest

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
