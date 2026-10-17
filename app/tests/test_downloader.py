"""Tests for the pure helpers in downloader.py (no network, no ffmpeg)."""

import pytest
import yt_dlp

import downloader


# ---------- URL helpers ----------

@pytest.mark.parametrize("url, expected", [
    ("https://www.tiktok.com/@user/video/123", True),
    ("https://VM.TIKTOK.COM/abc/", True),
    ("https://www.youtube.com/watch?v=abc", False),
])
def test_is_tiktok(url, expected):
    """
    Checks that TikTok links are recognized regardless of case.

    Parameters:
    url (str): The link to check.
    expected (bool): The expected result.
    """
    assert downloader.is_tiktok(url) is expected


# ---------- Progress ----------

def test_download_percent_uses_total_bytes():
    """Checks the percentage when the exact total is known."""
    assert downloader.download_percent({"downloaded_bytes": 50, "total_bytes": 200}) == 25


def test_download_percent_falls_back_to_estimate():
    """Checks the percentage when only an estimated total is known."""
    assert downloader.download_percent({"downloaded_bytes": 30, "total_bytes_estimate": 60}) == 50


def test_download_percent_unknown_total():
    """Checks that an unknown total size gives None instead of crashing."""
    assert downloader.download_percent({"downloaded_bytes": 30}) is None


# ---------- Sizes ----------

@pytest.mark.parametrize("num_bytes, expected", [
    (None, None),
    (0, None),
    (512, "512B"),
    (2048, "2.0KB"),
    (5 * 1024 * 1024, "5.0MB"),
    (3 * 1024 ** 3, "3.0GB"),
    (5000 * 1024 ** 3, "5000.0GB"),
])
def test_format_size(num_bytes, expected):
    """
    Checks byte counts are formatted with the right unit.

    Parameters:
    num_bytes (int/None): Size in bytes.
    expected (str/None): The expected display string.
    """
    assert downloader.format_size(num_bytes) == expected


def test_known_size_prefers_exact_size():
    """Checks filesize wins over filesize_approx."""
    assert downloader.known_size({"filesize": 10, "filesize_approx": 99}) == 10
    assert downloader.known_size({"filesize_approx": 99}) == 99
    assert downloader.known_size({}) is None


# ---------- Formats ----------

FORMATS = [
    {"format_id": "a", "height": None, "vcodec": "none", "filesize": 1024 * 1024},
    {"format_id": "v720", "height": 720, "vcodec": "avc1", "filesize": 9 * 1024 * 1024},
    {"format_id": "v720b", "height": 720, "vcodec": "vp9", "filesize": 4 * 1024 * 1024},
    {"format_id": "v1080", "height": 1080, "vcodec": "avc1", "tbr": 800},
    {"format_id": "x", "height": 480},  # X/Twitter style: no vcodec at all
    {"format_id": "storyboard", "height": 90, "vcodec": "none"},
]


def test_get_available_resolutions():
    """Checks heights are distinct, sorted highest first, and skip no-video formats."""
    info = {"formats": FORMATS}
    assert downloader.get_available_resolutions(info) == [1080, 720, 480]


def test_get_resolutions_with_sizes():
    """Checks each height gets the largest video size plus the best audio size."""
    info = {"formats": FORMATS, "duration": 10}
    result = dict(downloader.get_resolutions_with_sizes(info))
    # 720p: largest reported video (9MB) + best audio (1MB)
    assert result[720] == "10.0MB"
    # 1080p: 800 kbps * 10 s = 1,000,000 bytes estimate, + 1MB audio
    assert result[1080] == downloader.format_size(1_000_000 + 1024 * 1024)
    # 480p: no size information at all
    assert result[480] is None


def test_estimate_tiktok_size_skips_watermarked_format():
    """Checks the watermarked "download" format is ignored when estimating."""
    info = {"formats": [
        {"format_id": downloader.TIKTOK_WATERMARK_FORMAT_ID, "filesize": 50 * 1024 * 1024},
        {"format_id": "h264", "filesize": 2 * 1024 * 1024},
    ]}
    assert downloader.estimate_tiktok_size(info) == "2.0MB"


def test_format_opts_audio_extracts_mp3():
    """Checks audio downloads convert to 192 kbps MP3."""
    opts = downloader._format_opts("audio", None)
    extract = opts["postprocessors"][0]
    assert extract["key"] == "FFmpegExtractAudio"
    assert extract["preferredcodec"] == "mp3"


def test_format_opts_tiktok_excludes_watermark():
    """Checks TikTok downloads exclude the watermarked format."""
    opts = downloader._format_opts("tiktok", None)
    assert f"format_id!={downloader.TIKTOK_WATERMARK_FORMAT_ID}" in opts["format"]


def test_format_opts_video_with_resolution():
    """Checks a chosen resolution caps the height and output is mp4."""
    opts = downloader._format_opts("video", "720")
    assert "[height<=720]" in opts["format"]
    assert opts["merge_output_format"] == "mp4"


def test_format_opts_video_best():
    """Checks no resolution means no height cap."""
    assert "height" not in downloader._format_opts("video", None)["format"]


# ---------- Titles ----------

@pytest.mark.parametrize("title, expected", [
    ("VANNDA - DO YOU (OFFICIAL VIDEO)", "VANNDA - DO YOU"),
    ("Song Name [Lyrics]", "Song Name"),
    ("Artist | Track (Official Audio) [4K]", "Artist   Track"),
    ("Plain Title", "Plain Title"),
    (None, ""),
])
def test_clean_title_for_search(title, expected):
    """
    Checks bracketed junk is stripped from titles before a music search.

    Parameters:
    title (str/None): The raw title.
    expected (str): The cleaned title.
    """
    assert downloader.clean_title_for_search(title) == expected


def test_normalize_for_match():
    """Checks text is reduced to lowercase letters and digits."""
    assert downloader._normalize_for_match("Don't Stop!") == "dontstop"
    assert downloader._normalize_for_match(None) == ""


# ---------- Retry logic ----------

class FakeYDL:
    """A stand-in for yt_dlp.YoutubeDL that fails a set number of times."""

    def __init__(self, errors):
        """
        Stores the errors to raise, one per call, before succeeding.

        Parameters:
        errors (list): Messages to raise as DownloadError, in order.
        """
        self.errors = list(errors)
        self.calls = 0

    def extract_info(self, url, download):
        """
        Raises the next queued error, or returns a fake info dict.

        Parameters:
        url (str): The video link.
        download (bool): Ignored.

        Returns:
        dict: {"title": "ok"} once the errors run out.
        """
        self.calls += 1
        if self.errors:
            raise yt_dlp.utils.DownloadError(self.errors.pop(0))
        return {"title": "ok"}


def test_retry_recovers_from_transient_error():
    """Checks a transient TikTok error is retried until it succeeds."""
    ydl = FakeYDL(["Unable to extract universal data for rehydration"] * 2)
    assert downloader.extract_info_with_retry(ydl, "u", False, delay=0) == {"title": "ok"}
    assert ydl.calls == 3


def test_retry_does_not_retry_real_errors():
    """Checks a non-transient error is raised straight away."""
    ydl = FakeYDL(["Video unavailable"])
    with pytest.raises(yt_dlp.utils.DownloadError):
        downloader.extract_info_with_retry(ydl, "u", False, delay=0)
    assert ydl.calls == 1


def test_retry_gives_up_after_last_attempt():
    """Checks retries stop at the attempt limit."""
    ydl = FakeYDL(["status code 100001"] * 10)
    with pytest.raises(yt_dlp.utils.DownloadError):
        downloader.extract_info_with_retry(ydl, "u", False, attempts=3, delay=0)
    assert ydl.calls == 3


# ---------- Cookies ----------

def test_cookie_opts_missing_file(tmp_path, monkeypatch):
    """Checks a missing cookies.txt gives no cookie option."""
    monkeypatch.setattr(downloader, "COOKIE_FILE", str(tmp_path / "cookies.txt"))
    assert downloader._cookie_opts() == {}


def test_cookie_opts_invalid_file(tmp_path, monkeypatch):
    """Checks a cookies.txt that isn't a Netscape export is ignored."""
    path = tmp_path / "cookies.txt"
    path.write_text("not a cookie file\n")
    monkeypatch.setattr(downloader, "COOKIE_FILE", str(path))
    assert downloader._cookie_opts() == {}


def test_cookie_opts_valid_file(tmp_path, monkeypatch):
    """Checks a real Netscape export is passed to yt-dlp."""
    path = tmp_path / "cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(downloader, "COOKIE_FILE", str(path))
    assert downloader._cookie_opts() == {"cookiefile": str(path)}


# ---------- Audio quality ----------

@pytest.mark.parametrize("quality", ["128", "192", "320"])
def test_format_opts_audio_bitrates(quality):
    """
    Checks each MP3 bitrate is passed to the audio extractor.

    Parameters:
    quality (str): The chosen bitrate.
    """
    extract = downloader._format_opts("audio", None, audio_quality=quality)["postprocessors"][0]
    assert extract["preferredcodec"] == "mp3"
    assert extract["preferredquality"] == quality


def test_format_opts_audio_m4a():
    """Checks M4A picks an m4a stream and converts to m4a."""
    opts = downloader._format_opts("audio", None, audio_quality="m4a")
    assert opts["postprocessors"][0]["preferredcodec"] == "m4a"
    assert "m4a" in opts["format"]


def test_format_opts_audio_rejects_unknown():
    """Checks an unknown audio quality is refused."""
    with pytest.raises(ValueError):
        downloader._format_opts("audio", None, audio_quality="999")


# ---------- Trim ----------

@pytest.mark.parametrize("text, expected", [
    ("", None), (None, None), ("90", 90), ("1:30", 90),
    ("1:02:03", 3723), ("0:05.5", 5.5), (" 2:00 ", 120),
])
def test_parse_timestamp(text, expected):
    """
    Checks seconds, m:ss and h:mm:ss are understood.

    Parameters:
    text (str/None): The typed time.
    expected (float/None): Seconds.
    """
    assert downloader.parse_timestamp(text) == expected


@pytest.mark.parametrize("text", ["abc", "-5", "1:75", "1::2", "1:2:3:4"])
def test_parse_timestamp_invalid(text):
    """
    Checks malformed times are refused.

    Parameters:
    text (str): The typed time.
    """
    with pytest.raises(ValueError):
        downloader.parse_timestamp(text)


def test_validate_trim():
    """Checks valid ranges pass through and an empty range means no trim."""
    assert downloader.validate_trim(30, 75, 120) == (30, 75)
    assert downloader.validate_trim(None, None, 120) is None
    assert downloader.validate_trim(None, 60, None) == (None, 60)


@pytest.mark.parametrize("start, end, duration", [(75, 30, 120), (30, 30, 120), (130, None, 120)])
def test_validate_trim_rejects(start, end, duration):
    """
    Checks impossible ranges are refused.

    Parameters:
    start (float/None): Start seconds.
    end (float/None): End seconds.
    duration (float/None): Video length.
    """
    with pytest.raises(ValueError):
        downloader.validate_trim(start, end, duration)


def test_validate_trim_end_past_duration_is_ok():
    """Checks an end past the video's length just means "to the end"."""
    assert downloader.validate_trim(0, 5940, 120) == (0, 120)


@pytest.fixture
def capture_ydl_opts(tmp_path, monkeypatch):
    """
    Fakes yt-dlp and ffmpeg inside download_media and records the options used.

    Parameters:
    tmp_path (pathlib.Path): pytest's temp folder, used as out_dir.
    monkeypatch (pytest.MonkeyPatch): pytest's patcher.

    Returns:
    dict: Filled with the yt-dlp options once download_media runs.
    """
    captured = {}

    def fake_extract(opts, url, download, setup=None):
        captured.update(opts)
        (tmp_path / "Clip.mp4").write_bytes(b"video")
        return {"title": "Clip", "ext": "mp4"}, opts

    monkeypatch.setattr(downloader, "extract_info_with_fallback", fake_extract)
    monkeypatch.setattr(downloader, "normalize_for_social", lambda *a, **k: None)
    monkeypatch.setattr(downloader, "_fetch_cover_art", lambda url, out_dir: None)
    return captured


def test_download_media_passes_trim_to_ytdlp(tmp_path, capture_ydl_opts):
    """Checks a trim range makes yt-dlp download only that part."""
    downloader.download_media("https://x", "video", str(tmp_path), trim=(30, 75))
    assert "download_ranges" in capture_ydl_opts
    assert capture_ydl_opts["force_keyframes_at_cuts"] is True


def test_download_media_without_trim_downloads_everything(tmp_path, capture_ydl_opts):
    """Checks no trim means no download range."""
    downloader.download_media("https://x", "video", str(tmp_path))
    assert "download_ranges" not in capture_ydl_opts


def test_download_media_m4a_extension(tmp_path, capture_ydl_opts):
    """Checks an M4A download returns a .m4a path."""
    path = downloader.download_media("https://x", "audio", str(tmp_path), audio_quality="m4a")
    assert path.endswith("Clip.m4a")


# ---------- Subtitles ----------

def test_get_subtitle_languages():
    """Checks manual tracks come first, and only original-language auto captions are offered."""
    info = {
        "language": "en",
        "subtitles": {"km": [{"name": "Khmer"}], "en": [{"name": "English"}], "live_chat": [{}]},
        "automatic_captions": {"en": [{"name": "English"}], "fr": [{"name": "French"}]},
    }
    assert downloader.get_subtitle_languages(info) == [
        {"code": "en", "name": "English", "auto": False, "translated": False},
        {"code": "km", "name": "Khmer", "auto": False, "translated": False},
        {"code": "en", "name": "English", "auto": True, "translated": False},
    ]


def test_get_subtitle_languages_adds_english_and_khmer_translations():
    """Checks English and Khmer auto-translations are offered after the original captions."""
    info = {
        "language": "km",
        "automatic_captions": {
            "fr": [{"name": "French"}], "en": [{"name": "English"}], "km": [{"name": "Khmer"}],
        },
    }
    assert downloader.get_subtitle_languages(info) == [
        {"code": "km", "name": "Khmer", "auto": True, "translated": False},
        {"code": "en", "name": "English", "auto": True, "translated": True},
    ]


def test_get_subtitle_languages_orig_track_without_language():
    """Checks "-orig" auto tracks are offered when the video's language is unknown."""
    info = {"automatic_captions": {"km-orig": [{"name": "Khmer (Original)"}], "de": [{}]}}
    assert downloader.get_subtitle_languages(info) == [
        {"code": "km-orig", "name": "Khmer (Original)", "auto": True, "translated": False},
    ]


def test_get_subtitle_languages_empty():
    """Checks a video without subtitles gives an empty list."""
    assert downloader.get_subtitle_languages({}) == []


def test_subtitle_path_for():
    """Checks the .srt sits next to the video with the language in its name."""
    assert downloader.subtitle_path_for("/d/Clip.mp4", "km") == "/d/Clip.km.srt"


SRT = """1
00:00:10,000 --> 00:00:12,500
first

2
00:00:31,000 --> 00:00:33,000
second
line two

3
00:01:20,000 --> 00:01:22,000
third
"""


def test_shift_srt_moves_cues_and_drops_outside(tmp_path):
    """Checks a trimmed clip's subtitles start from the trim point."""
    path = tmp_path / "a.srt"
    path.write_text(SRT, encoding="utf-8")
    downloader.shift_srt(str(path), start=30, end=75)
    assert path.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:03,000\nsecond\nline two\n"
    )


def test_shift_srt_clamps_cue_crossing_start(tmp_path):
    """Checks a cue that began before the trim point starts at 0 instead of being lost."""
    path = tmp_path / "a.srt"
    path.write_text(SRT, encoding="utf-8")
    downloader.shift_srt(str(path), start=11, end=None)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("1\n00:00:00,000 --> 00:00:01,500\nfirst\n")
    assert "3\n00:01:09,000 --> 00:01:11,000\nthird\n" in text


def test_download_media_subtitle_opts(tmp_path, capture_ydl_opts):
    """Checks subtitle options are passed to yt-dlp and converted to srt."""
    downloader.download_media("https://x", "video", str(tmp_path),
                              subtitles={"lang": "km", "mode": "srt"})
    assert capture_ydl_opts["subtitleslangs"] == ["km"]
    assert capture_ydl_opts["writesubtitles"] is True
    assert capture_ydl_opts["writeautomaticsub"] is True
    keys = [p["key"] for p in capture_ydl_opts.get("postprocessors", [])]
    assert "FFmpegSubtitlesConvertor" in keys


def test_download_media_audio_ignores_subtitles(tmp_path, capture_ydl_opts):
    """Checks audio downloads never ask for subtitles."""
    downloader.download_media("https://x", "audio", str(tmp_path),
                              subtitles={"lang": "km", "mode": "srt"})
    assert "subtitleslangs" not in capture_ydl_opts


def test_burn_without_subtitle_file_fails(tmp_path, capture_ydl_opts):
    """Checks burn-in fails clearly when the language produced no subtitle file."""
    with pytest.raises(ValueError, match="No subtitles"):
        downloader.download_media("https://x", "video", str(tmp_path),
                                  subtitles={"lang": "km", "mode": "burn"})


def test_burn_passes_subtitle_to_normalize(tmp_path, capture_ydl_opts, monkeypatch):
    """Checks the .srt is burned during the social re-encode and then removed."""
    srt = tmp_path / "Clip.km.srt"
    srt.write_text(SRT, encoding="utf-8")
    calls = {}
    monkeypatch.setattr(downloader, "normalize_for_social",
                        lambda path, **k: calls.update(k) or True)
    downloader.download_media("https://x", "video", str(tmp_path),
                              subtitles={"lang": "km", "mode": "burn"})
    assert calls["burn_subtitles"] == str(srt)
    assert not srt.exists()


def test_trim_shifts_saved_subtitles(tmp_path, capture_ydl_opts):
    """Checks an .srt saved with a trimmed clip lines up with the clip."""
    srt = tmp_path / "Clip.km.srt"
    srt.write_text(SRT, encoding="utf-8")
    downloader.download_media("https://x", "video", str(tmp_path), trim=(30, 75),
                              subtitles={"lang": "km", "mode": "srt"})
    assert srt.read_text(encoding="utf-8").startswith("1\n00:00:01,000 --> 00:00:03,000\n")


def test_burn_uses_complex_shaping(tmp_path, monkeypatch):
    """Checks burn-in uses libass complex shaping, which Khmer vowel signs need."""
    video = tmp_path / "Clip.mp4"
    video.write_bytes(b"video")
    srt = tmp_path / "Clip.km.srt"
    srt.write_text(SRT, encoding="utf-8")
    monkeypatch.setattr(downloader, "_already_social_safe", lambda path: True)
    monkeypatch.setattr(downloader, "_get_source_fps", lambda path: "30")
    monkeypatch.setattr(downloader, "_get_duration_seconds", lambda path: None)
    def fake_run(args, **kwargs):
        open(args[-1], "w").close()  # the .srt -> .ass conversion succeeds
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    commands = []

    class FakePopen:
        """Records the ffmpeg command and reports failure, so the original is kept."""

        returncode = 1
        stdout = iter(())

        def __init__(self, args, **kwargs):
            commands.append(args)

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setattr(downloader.subprocess, "Popen", FakePopen)
    downloader.normalize_for_social(str(video), burn_subtitles=str(srt))
    vf = commands[0][commands[0].index("-vf") + 1]
    assert "ass=burn_subs.ass:shaping=complex" in vf
    assert not (tmp_path / "burn_subs.ass").exists()


# ---------- Batch ----------

def no_playlists(url):
    """
    Fails the test if a plain link is treated as a playlist.

    Parameters:
    url (str): The link.
    """
    raise AssertionError(f"extract called for {url}")


def test_expand_batch_splits_and_dedupes():
    """Checks links are split on any whitespace, deduplicated, and non-links dropped."""
    text = "https://a\n\nhttps://b  https://a\nnot-a-link"
    assert downloader.expand_batch(text, extract=no_playlists) == ["https://a", "https://b"]


def test_expand_batch_expands_playlists():
    """Checks a playlist link is replaced by its videos, in order."""
    playlist = "https://youtube.com/playlist?list=X"

    def fake_extract(url):
        assert url == playlist
        return {"entries": [{"url": "https://v1"}, {"webpage_url": "https://v2"}, None]}

    text = f"https://v0\n{playlist}"
    assert downloader.expand_batch(text, extract=fake_extract) == [
        "https://v0", "https://v1", "https://v2",
    ]


def test_expand_batch_empty():
    """Checks text without links is refused."""
    with pytest.raises(ValueError, match="No links found"):
        downloader.expand_batch("hello", extract=no_playlists)


def test_expand_batch_limit():
    """Checks more than 50 links is refused, and exactly 50 is fine."""
    fifty = "\n".join(f"https://v{i}" for i in range(50))
    assert len(downloader.expand_batch(fifty, extract=no_playlists)) == 50
    with pytest.raises(ValueError, match="max 50"):
        downloader.expand_batch(fifty + "\nhttps://v50", extract=no_playlists)


# ---------- Review fixes ----------

def test_expand_batch_bad_playlist_is_a_clear_error():
    """Checks a playlist that can't be read gives a readable ValueError, not a crash."""
    def broken(url):
        raise yt_dlp.utils.DownloadError("ERROR: [youtube:tab] X: This playlist does not exist")
    with pytest.raises(ValueError, match="Couldn't read playlist"):
        downloader.expand_batch("https://youtube.com/playlist?list=X", extract=broken)


def test_expand_batch_keeps_video_links_from_playlists():
    """Checks a video copied from inside a playlist stays one video."""
    url = "https://www.youtube.com/watch?v=abc&list=RDabc&index=2"
    assert downloader.expand_batch(url, extract=no_playlists) == [url]


def test_extract_playlist_stops_after_limit(monkeypatch):
    """Checks huge playlists stop being read just past the limit."""
    captured = {}

    class FakeYDL:
        """Records the options instead of contacting YouTube."""

        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            return {"entries": []}

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    downloader._extract_playlist("https://youtube.com/playlist?list=X")
    assert captured["playlistend"] == downloader.BATCH_LIMIT + 1


def test_burn_failure_fails_the_download(tmp_path, capture_ydl_opts, monkeypatch):
    """Checks a burn-in that didn't work fails the job instead of returning a plain video."""
    (tmp_path / "Clip.km.srt").write_text(SRT, encoding="utf-8")
    monkeypatch.setattr(downloader, "normalize_for_social", lambda path, **k: False)
    with pytest.raises(ValueError, match="burn the subtitles"):
        downloader.download_media("https://x", "video", str(tmp_path),
                                  subtitles={"lang": "km", "mode": "burn"})


def test_normalize_reports_failed_burn(tmp_path, monkeypatch):
    """Checks normalize_for_social returns False when the burn re-encode fails."""
    video = tmp_path / "Clip.mp4"
    video.write_bytes(b"video")
    srt = tmp_path / "Clip.km.srt"
    srt.write_text(SRT, encoding="utf-8")
    monkeypatch.setattr(downloader, "_get_source_fps", lambda path: "30")
    monkeypatch.setattr(downloader, "_get_duration_seconds", lambda path: None)

    def fake_run(args, **kwargs):
        open(args[-1], "w").close()  # the .ass conversion succeeds
        return type("R", (), {"returncode": 0})()

    class FailingPopen:
        """An ffmpeg run that fails."""

        returncode = 1
        stdout = iter(())

        def __init__(self, args, **kwargs):
            pass

        def communicate(self, timeout=None):
            return "", "boom"

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    monkeypatch.setattr(downloader.subprocess, "Popen", FailingPopen)
    assert downloader.normalize_for_social(str(video), burn_subtitles=str(srt)) is False
    assert not (tmp_path / "burn_subs.ass").exists()


def test_get_subtitle_languages_finds_name_in_any_format():
    """Checks the language name is taken from whichever format has one (YouTube's first often has none)."""
    info = {"language": "en", "automatic_captions": {
        "en": [{"ext": "vtt"}, {"ext": "json3", "name": "English"}],
        "km": [{"ext": "json3", "name": "Khmer"}],
    }}
    assert [t["name"] for t in downloader.get_subtitle_languages(info)] == ["English", "Khmer"]


# ---------- YouTube 403 ----------

YT_403 = "ERROR: unable to download video data: HTTP Error 403: Forbidden"


def test_retry_recovers_from_youtube_403():
    """Checks a one-off 403 from YouTube is retried instead of failing the download."""
    ydl = FakeYDL([YT_403])
    assert downloader.extract_info_with_retry(ydl, "u", True, delay=0) == {"title": "ok"}
    assert ydl.calls == 2


def test_repeated_403_falls_back_to_android_client(monkeypatch):
    """Checks a 403 that keeps happening switches to YouTube's Android client, like sign-in errors do."""
    monkeypatch.setattr(downloader.time, "sleep", lambda s: None)
    used = []

    class FakeYoutubeDL:
        """Fails with 403 on the default client, works on the Android client."""

        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            android = "extractor_args" in self.opts
            used.append("android" if android else "default")
            if not android:
                raise yt_dlp.utils.DownloadError(YT_403)
            return {"title": "ok"}

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    info, opts = downloader.extract_info_with_fallback({"quiet": True}, "https://youtu.be/x", True)
    assert info == {"title": "ok"}
    assert opts["extractor_args"] == {"youtube": {"player_client": ["android"]}}
    assert used[-1] == "android" and used.count("default") >= 2


# ---------- Trim relay ----------

GOOGLEVIDEO = "https://rr1.googlevideo.com/videoplayback?id=1"


def youtube_section(parts, start=0, end=214, duration=21636):
    """
    Builds the info dict yt-dlp hands to before_dl hooks for a trimmed YouTube download.

    Parameters:
    parts (list): Format dicts; two or more means video + audio.
    start (int/float): Clip start in seconds.
    end (int/float): Clip end in seconds.
    duration (int/float): Full video length in seconds.

    Returns:
    dict: The info dict.
    """
    info = {"extractor_key": "Youtube", "duration": duration,
            "section_start": start, "section_end": end}
    if len(parts) > 1:
        info["requested_formats"] = parts
    else:
        info.update(parts[0])
    return info


def fmt(format_id, size, url=GOOGLEVIDEO, **extra):
    """
    Builds one format dict.

    Parameters:
    format_id (str): The format id.
    size (int/None): filesize in bytes.
    url (str): Stream link.
    extra (dict): More fields.

    Returns:
    dict: The format.
    """
    return {"format_id": format_id, "url": url, "protocol": "https",
            "filesize": size, "http_headers": {"User-Agent": "x"}, **extra}


class FakeRelay:
    """Records added streams instead of serving them."""

    def __init__(self):
        """Starts with nothing added."""
        self.added = []
        self.watchers = []
        self.failed = False
        self.watch_error = None
        self.stopped = False

    def stop_watching(self):
        """Records that reports were stopped."""
        self.stopped = True

    def add(self, url, headers, size, refresh=None):
        """
        Records a stream.

        Parameters:
        url (str): Upstream link.
        headers (dict): Upstream headers.
        size (int): File size.
        refresh (callable/None): Link refresher.

        Returns:
        str: A fake local link.
        """
        self.added.append((url, headers, size, refresh))
        return f"http://127.0.0.1:1/{len(self.added) - 1}"

    def watch(self, callback, interval=0.5):
        """
        Records the watcher.

        Parameters:
        callback (callable): The watcher.
        interval (int/float): Unused.
        """
        self.watchers.append(callback)


def test_part_sizes_for_video_and_audio():
    """Checks each separately downloaded part's size is listed, None when unknown."""
    info = {"requested_formats": [fmt("398", 500), {"format_id": "140", "filesize_approx": 300}, {}]}
    assert downloader.part_sizes(info) == [500, 300, None]


def test_part_sizes_for_one_format():
    """Checks a single-format download is one part."""
    assert downloader.part_sizes(fmt("251", 1000)) == [1000]


@pytest.mark.parametrize("total, duration, start, end, expected", [
    (1000, 100, 0, 10, 100),
    (1000, 100, 50, None, 500),
    (1000, 100, 90, 500, 100),
    (None, 100, 0, 10, None),
    (1000, None, 0, 10, None),
])
def test_clip_estimate(total, duration, start, end, expected):
    """
    Checks a clip's size is its share of the whole file.

    Parameters:
    total (int/None): Whole file bytes.
    duration (int/None): Whole video seconds.
    start (int): Clip start.
    end (int/None): Clip end, None for the end of the video.
    expected (int/None): Expected bytes.
    """
    assert downloader.clip_estimate(total, duration, start, end) == expected


def test_youtube_trim_is_relayable():
    """Checks a trimmed YouTube download with known sizes goes through the relay."""
    assert downloader.is_relayable(youtube_section([fmt("398", 500), fmt("140", 300)]))


@pytest.mark.parametrize("info", [
    {"extractor_key": "Youtube", **fmt("251", 1000)},
    youtube_section([fmt("251", None)]),
    youtube_section([fmt("251", 1000, url="https://example.com/a.webm")]),
    youtube_section([fmt("251", 1000, protocol="m3u8_native")]),
    {**youtube_section([fmt("251", 1000)]), "extractor_key": "TikTok"},
])
def test_not_relayable(info):
    """
    Checks untrimmed, unknown-size, non-googlevideo, non-https and non-YouTube downloads skip the relay.

    Parameters:
    info (dict): The info dict.
    """
    assert not downloader.is_relayable(info)


def test_before_download_reports_part_sizes():
    """Checks the hook gets a plan with every part's size before downloading starts."""
    events = []
    pp = downloader.BeforeDownloadPP(events.append)
    pp.run({"requested_formats": [fmt("137", 800), fmt("140", 200)]})
    assert events == [{"status": "plan", "part_sizes": [800, 200]}]
    assert not pp.relayed


def test_before_download_routes_trim_through_relay():
    """Checks a YouTube trim's links are swapped for relay links and the plan is the clip estimate."""
    events, relay = [], FakeRelay()
    pp = downloader.BeforeDownloadPP(events.append, relay=relay)
    info = youtube_section([fmt("398", 1000, url=GOOGLEVIDEO + "&v"), fmt("140", 500, url=GOOGLEVIDEO + "&a")],
                           start=0, end=10, duration=100)
    _, info = pp.run(info)

    assert pp.relayed
    assert [f["url"] for f in info["requested_formats"]] == ["http://127.0.0.1:1/0", "http://127.0.0.1:1/1"]
    assert [(u, s) for u, _, s, _ in relay.added] == [(GOOGLEVIDEO + "&v", 1000), (GOOGLEVIDEO + "&a", 500)]
    assert events == [{"status": "plan", "part_sizes": [150 + 2 * downloader.RELAY_OVERHEAD_BYTES]}]


def test_relay_watcher_reports_downloading_progress():
    """Checks relay byte counts reach the hook as yt-dlp-style "downloading" updates."""
    events, relay = [], FakeRelay()
    pp = downloader.BeforeDownloadPP(events.append, relay=relay)
    pp.run(youtube_section([fmt("251", 1000)], start=0, end=10, duration=100))

    estimate = 100 + downloader.RELAY_OVERHEAD_BYTES
    relay.watchers[0](40, 20.0)
    assert events[-1] == {"status": "downloading", "downloaded_bytes": 40,
                          "total_bytes_estimate": estimate, "speed": 20.0}
    relay.watchers[0](estimate + 30, 20.0)
    assert events[-1]["total_bytes_estimate"] == estimate + 30


def test_before_download_leaves_other_trims_alone():
    """Checks a trim the relay can't serve keeps its original link."""
    relay = FakeRelay()
    pp = downloader.BeforeDownloadPP(lambda d: None, relay=relay)
    _, info = pp.run(youtube_section([fmt("251", None)]))
    assert info["url"] == GOOGLEVIDEO
    assert not relay.added and not pp.relayed


def relay_run_fake(tmp_path, outcomes):
    """
    Builds a stand-in for extract_info_with_fallback that runs the before_dl hook.

    Parameters:
    tmp_path (pathlib.Path): Folder where the "downloaded" file is written.
    outcomes (list): Per call, "ok", "fail" (DownloadError) or "relay_failed" (succeeds but the relay broke).

    Returns:
    tuple: (fake function, list of BeforeDownloadPP seen per call).
    """
    seen = []

    class FakeYDL:
        def add_post_processor(self, pp, when):
            assert when == "before_dl"
            seen.append(pp)

        def add_progress_hook(self, hook):
            pass

    def fake_extract(opts, url, download, setup=None):
        # A retry must start clean: no leftovers from the broken attempt
        assert not seen or not (tmp_path / "Clip.mp3").exists()
        setup(FakeYDL())
        pp = seen[-1]
        pp.run(youtube_section([fmt("251", 1000)]))
        outcome = outcomes[len(seen) - 1]
        if outcome == "fail":
            raise yt_dlp.utils.DownloadError("ERROR: ffmpeg exited with code 251")
        if outcome == "relay_failed":
            pp.relay.failed = True
        (tmp_path / "Clip.mp3").write_bytes(b"audio")
        return {"title": "Clip", "ext": "webm"}, opts

    return fake_extract, seen


@pytest.fixture
def no_audio_extras(monkeypatch):
    """
    Skips cover art for audio downloads.

    Parameters:
    monkeypatch (pytest.MonkeyPatch): pytest's patcher.
    """
    monkeypatch.setattr(downloader, "_fetch_cover_art", lambda url, out_dir: None)


def test_trim_uses_the_relay(tmp_path, monkeypatch, no_audio_extras):
    """Checks a trimmed download runs once, through the relay, when it works."""
    fake, seen = relay_run_fake(tmp_path, ["ok"])
    monkeypatch.setattr(downloader, "extract_info_with_fallback", fake)
    path = downloader.download_media("https://youtu.be/x", "audio", str(tmp_path), trim=(0, 214))
    assert path.endswith("Clip.mp3")
    assert len(seen) == 1 and seen[0].relayed


@pytest.mark.parametrize("first", ["fail", "relay_failed"])
def test_trim_falls_back_when_the_relay_breaks(tmp_path, monkeypatch, no_audio_extras, first):
    """
    Checks a relay failure (ffmpeg error, or a cut-short stream) redoes the download the old way.

    Parameters:
    first (str): How the relayed attempt goes wrong.
    """
    fake, seen = relay_run_fake(tmp_path, [first, "ok"])
    monkeypatch.setattr(downloader, "extract_info_with_fallback", fake)
    downloader.download_media("https://youtu.be/x", "audio", str(tmp_path), trim=(0, 214))
    assert len(seen) == 2
    assert seen[0].relayed and seen[1].relay is None


def test_trim_cancel_is_not_retried(tmp_path, monkeypatch, no_audio_extras):
    """Checks a job cancelled during a relayed download stops instead of falling back."""
    fake, seen = relay_run_fake(tmp_path, ["fail"])

    def cancelling(opts, url, download, setup=None):
        try:
            return fake(opts, url, download, setup)
        finally:
            seen[-1].relay.watch_error = yt_dlp.utils.DownloadCancelled("Job deleted")

    monkeypatch.setattr(downloader, "extract_info_with_fallback", cancelling)
    with pytest.raises(yt_dlp.utils.DownloadCancelled):
        downloader.download_media("https://youtu.be/x", "audio", str(tmp_path), trim=(0, 214))
    assert len(seen) == 1


def test_untrimmed_download_has_no_relay(tmp_path, capture_ydl_opts, monkeypatch):
    """Checks normal downloads still report their plan but never start a relay."""
    seen = []

    def fake_extract(opts, url, download, setup=None):
        class FakeYDL:
            def add_post_processor(self, pp, when):
                seen.append(pp)

            def add_progress_hook(self, hook):
                pass
        setup(FakeYDL())
        (tmp_path / "Clip.mp4").write_bytes(b"video")
        return {"title": "Clip", "ext": "mp4"}, opts

    monkeypatch.setattr(downloader, "extract_info_with_fallback", fake_extract)
    downloader.download_media("https://x", "video", str(tmp_path))
    assert seen and seen[0].relay is None


def test_fallback_setup_runs_on_every_ydl(monkeypatch):
    """Checks extract_info_with_fallback hands each YoutubeDL it makes to setup, Android retry included."""
    monkeypatch.setattr(downloader.time, "sleep", lambda s: None)
    setups = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            if "extractor_args" not in self.opts:
                raise yt_dlp.utils.DownloadError(YT_403)
            return {"title": "ok"}

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    downloader.extract_info_with_fallback({}, "https://youtu.be/x", True, setup=setups.append)
    assert len(setups) == 2


def test_relay_reports_stop_when_the_download_finishes():
    """Checks yt-dlp's "finished" stops the relay's reports, so converting can't look like downloading."""
    relay = FakeRelay()
    pp = downloader.BeforeDownloadPP(lambda d: None, relay=relay)
    pp.run(youtube_section([fmt("251", 1000)]))

    hooks = []

    class FakeYDL:
        def add_post_processor(self, pp, when):
            pass

        def add_progress_hook(self, hook):
            hooks.append(hook)

    downloader._relay_setup(None, relay, None, [])(FakeYDL())
    hooks[0]({"status": "downloading"})
    hooks[0]({"status": "finished", "info_dict": {"ext": "vtt"}})
    assert not relay.stopped
    hooks[0]({"status": "finished", "info_dict": {"format_id": "251"}})
    assert relay.stopped


@pytest.mark.parametrize("d, expected", [
    ({"status": "finished", "info_dict": {"ext": "vtt", "url": "u"}}, True),
    ({"status": "finished", "info_dict": {"format_id": "251"}}, False),
    ({"status": "downloading", "downloaded_bytes": 1}, False),
])
def test_is_subtitle_progress(d, expected):
    """
    Checks subtitle file downloads are told apart from the video's own streams.

    Parameters:
    d (dict): A progress update.
    expected (bool): True for a subtitle file.
    """
    assert downloader.is_subtitle_progress(d) is expected


# ---------- Site names ----------

@pytest.mark.parametrize("info, expected", [
    ({"extractor_key": "Youtube"}, "YouTube"),
    ({"extractor_key": "YoutubeTab"}, "YouTube"),
    ({"extractor_key": "TikTok"}, "TikTok"),
    ({"extractor_key": "Soundcloud"}, "SoundCloud"),
    ({"extractor_key": "ArchiveOrg"}, "Internet Archive"),
    ({"extractor_key": "Twitter"}, "X (Twitter)"),
    ({"extractor_key": "Vimeo"}, "Vimeo"),
    ({"extractor_key": "Generic", "webpage_url_domain": "news.example.com"}, "news.example.com"),
    ({"extractor_key": "Generic", "webpage_url": "https://www.example.org/a"}, "example.org"),
    ({}, None),
])
def test_site_name(info, expected):
    """
    Checks a link's site gets a readable name, falling back to its domain.

    Parameters:
    info (dict): yt-dlp info dict.
    expected (str/None): The expected name.
    """
    assert downloader.site_name(info) == expected


# ---------- Playlists in single mode ----------

def test_get_video_info_reads_one_video_and_flat_playlists(monkeypatch):
    """Checks a video link inside a playlist previews only that video, and playlists are read quickly."""
    seen = {}

    def fake_extract(opts, url, download, setup=None):
        seen.update(opts)
        return {"title": "x"}, opts

    monkeypatch.setattr(downloader, "extract_info_with_fallback", fake_extract)
    downloader.get_video_info("https://youtube.com/watch?v=a&list=b")
    assert seen["noplaylist"] is True
    assert seen["extract_flat"] == "in_playlist"


def test_download_media_downloads_one_video_not_its_playlist(tmp_path, capture_ydl_opts):
    """Checks a single download of a watch?v=...&list=... link gets just that video."""
    downloader.download_media("https://youtube.com/watch?v=a&list=b", "video", str(tmp_path))
    assert capture_ydl_opts["noplaylist"] is True
