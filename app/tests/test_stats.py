"""Tests for stats.py: saving finished downloads and adding them up."""

import stats

DAY = 24 * 3600
NOW = 1_800_000_000


def test_summary_without_file(tmp_path):
    """Checks a missing stats file reads as no downloads."""
    assert stats.summary(str(tmp_path / "none.jsonl"), now=NOW) == {
        "total": 0, "this_week": 0, "video_percent": 0, "audio_percent": 0, "top_site": None,
    }


def test_record_creates_folder(tmp_path):
    """Checks record() creates the stats folder when it is missing."""
    path = str(tmp_path / "new" / "stats.jsonl")
    stats.record(path, "video", "YouTube", 10, now=NOW)
    assert stats.summary(path, now=NOW)["total"] == 1


def test_summary_adds_up(tmp_path):
    """Checks the totals, this week, the video/audio split and the most used site."""
    path = str(tmp_path / "stats.jsonl")
    stats.record(path, "video", "YouTube", 100, now=NOW)
    stats.record(path, "tiktok", "TikTok", 100, now=NOW - DAY)
    stats.record(path, "audio", "YouTube", 100, now=NOW - 2 * DAY)
    stats.record(path, "video", "TikTok", 100, now=NOW - 8 * DAY)  # last week
    stats.record(path, "video", "YouTube", 100, now=NOW - 30 * DAY)

    assert stats.summary(path, now=NOW) == {
        "total": 5, "this_week": 3, "video_percent": 80, "audio_percent": 20, "top_site": "YouTube",
    }


def test_summary_skips_broken_lines(tmp_path):
    """Checks a broken line in the stats file is skipped, not fatal."""
    path = tmp_path / "stats.jsonl"
    stats.record(str(path), "audio", "SoundCloud", 5, now=NOW)
    with open(path, "a") as f:
        f.write("not json\n{\"type\": \n")
    stats.record(str(path), "audio", "SoundCloud", 5, now=NOW)

    summary = stats.summary(str(path), now=NOW)
    assert summary["total"] == 2
    assert summary["audio_percent"] == 100


def test_record_saves_no_link_or_title(tmp_path):
    """Checks a saved line holds only time, type, site and size."""
    path = tmp_path / "stats.jsonl"
    stats.record(str(path), "video", "YouTube", 42, now=NOW)
    assert path.read_text() == '{"time": 1800000000, "type": "video", "site": "YouTube", "bytes": 42}\n'
