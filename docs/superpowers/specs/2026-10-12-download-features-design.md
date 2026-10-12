# Download features: audio quality, trim, subtitles, history, batch

Date: 2026-10-12
Status: approved design, awaiting spec review
Scope: web app only (`app/app.py`, `app/downloader.py`, new `app/jobs.py`,
`app/templates/index.html`, `app/static/script.js`, `app/static/style.css`).
The Telegram bot keeps its current behaviour.

## Goals

Add five features to the web app:

1. **Audio quality choice** - MP3 at 128 / 192 / 320 kbps, or M4A.
2. **Trim a clip** - optional start/end time for video, audio and TikTok.
3. **Subtitles** - pick a language, then either save an `.srt` file next to
   the video or burn the subtitles into the picture.
4. **Download history** - each browser sees its own recent downloads and can
   save them again or delete them. Files are kept for 24 hours.
5. **Playlist / batch mode** - paste several links or a playlist link, get
   one `.zip`. At most 50 items.

Success: every feature works end to end from the web page against real
YouTube and TikTok links, two visitors downloading at the same time no
longer see each other's progress, and `downloads/` no longer grows without
limit.

## Non-goals

- No changes to the Telegram bot or the Windows desktop build.
- No accounts, no shared/global history, no "list all jobs" endpoint.
- No parallel downloads inside a batch (items run one after another).
- Not fixing `debug=True` with the public tunnel - tracked separately.

## Architecture

### Job system (`app/jobs.py`, new)

Every download, single or batch, is a **job**.

`Job` fields:

| Field | Type | Notes |
|---|---|---|
| `id` | str | `uuid4().hex`, 32 lowercase hex chars |
| `kind` | str | `"single"` or `"batch"` |
| `dtype` | str | `"video"`, `"audio"` or `"tiktok"` |
| `options` | dict | resolution, audio_quality, trim, subtitles (see below) |
| `status` | str | `queued`, `downloading`, `processing`, `optimizing`, `zipping`, `done`, `error`, `interrupted` |
| `percent` | int | 0-100, overall for batches |
| `title` | str/None | video title, or `"Batch (N items)"` |
| `files` | list[str] | file names inside the job folder, main file first |
| `error` | str/None | user-facing error message |
| `item_index`, `item_count` | int | batch progress, e.g. 3 / 12; 0 / 1 for single |
| `failed_items` | list[dict] | batch only: `{"url", "error"}` per skipped item |
| `created` | float | `time.time()` at creation |

`JobStore(root_dir)`:

- `create(kind, dtype, options) -> Job` - makes `root_dir/<id>/`, writes `job.json`.
- `get(job_id) -> Job | None` - rejects ids that are not 32 hex chars.
- `update(job_id, **fields)` - updates in memory and rewrites `job.json`.
- `delete(job_id)` - removes the folder and the in-memory entry.
- `load()` - on startup, reads every `*/job.json`; jobs whose status is not
  `done`/`error` become `interrupted`. Folders without a readable
  `job.json` are deleted.
- `cleanup(max_age=24*3600)` - deletes jobs older than `max_age`.
- `start_cleanup_thread(interval=600)` - daemon thread calling `cleanup()`.

All methods take one `threading.Lock`. `job.json` is written to a temp
file and swapped in with `os.replace` so a crash never leaves half a file.

Job progress is updated only at milestones (status change, whole-percent
change), so `job.json` is not rewritten on every yt-dlp tick.

### Downloader options (`app/downloader.py`)

`download_media(url, dtype, out_dir, progress_hook=None, resolution=None,
audio_quality="192", trim=None, subtitles=None)`

All new parameters default to today's behaviour, so the bot is unaffected.
Return value stays the path of the main file. The `.srt` file (if any) is
found by the new helper `subtitle_path_for(video_path, lang)`.

- **`audio_quality`**: `"128"`, `"192"`, `"320"` -> MP3 at that bitrate;
  `"m4a"` -> `FFmpegExtractAudio` with `preferredcodec="m4a"` and the final
  extension `.m4a`. Cover art is embedded for MP3 as today; for M4A it is
  embedded only if the existing ffmpeg-based embed works with mp4
  containers, otherwise skipped (verified during implementation).
  Unknown values raise `ValueError`.
- **`trim`**: `(start_seconds, end_seconds)`, either may be `None`
  (meaning start of video / end of video). Passed to yt-dlp as
  `download_ranges=download_range_func(None, [(start, end)])` with
  `force_keyframes_at_cuts=True`, so only that part is downloaded.
  New helper `parse_timestamp(text) -> float | None` accepts `"90"`,
  `"1:30"`, `"1:02:03"` and `""` (None); anything else raises `ValueError`.
  New helper `validate_trim(start, end, duration)` raises `ValueError` when
  end <= start, or start >= duration when the duration is known.
- **`subtitles`**: `{"lang": "km", "mode": "srt" | "burn"}` or `None`.
  Ignored for `dtype == "audio"`. yt-dlp options: `writesubtitles`,
  `writeautomaticsub`, `subtitleslangs=[lang]`, plus the
  `FFmpegSubtitlesConvertor` postprocessor to `srt`.
  - `srt`: the `.srt` stays next to the video.
  - `burn`: `normalize_for_social` gets a new `burn_subtitles=<srt path>`
    argument; it forces the re-encode path and adds
    `-vf subtitles=<path>` (combined with the existing scale filter), then
    deletes the `.srt`. If no subtitle file was produced, the download fails
    with a clear message instead of silently returning a video without them.
- **Subtitle languages for the preview**: new
  `get_subtitle_languages(info) -> list[dict]` returning
  `{"code", "name", "auto"}` from `info["subtitles"]` (manual, listed first)
  and `info["automatic_captions"]` (auto, original-language tracks only,
  not every auto-translated language), sorted by name within each group.
- **Batch**: new `expand_batch(text, limit=50) -> list[str]`.
  Splits on whitespace/newlines, keeps `http(s)://` entries, removes
  duplicates (order kept). Any entry that is a playlist (yt-dlp
  `extract_flat`) is expanded into its video URLs. Raises `ValueError` when
  there are no links or more than `limit` after expansion.

### Routes (`app/app.py`)

| Route | Behaviour |
|---|---|
| `GET /` | unchanged |
| `POST /info` | as today + `"subtitles": get_subtitle_languages(info)` |
| `POST /download` | form: `mode` (`single`/`batch`), `url` or `urls`, `type`, `resolution`, `audio_quality`, `trim_start`, `trim_end`, `sub_lang`, `sub_mode`. Validates, creates a job, starts a worker thread, returns `{"job_id": ...}`; validation errors return `400 {"error": ...}` |
| `GET /progress/<job_id>` | SSE of that job's public fields every 0.5 s until `done`/`error`/`interrupted`; 404 for unknown ids |
| `GET /files/<job_id>/<name>` | `send_from_directory(job folder, name, as_attachment=True)`; 404 if job or file unknown. Files are not deleted on send |
| `POST /jobs/lookup` | JSON `{"ids": [...]}` (max 100) -> `{"jobs": {id: public fields or null}}` |
| `DELETE /jobs/<job_id>` | deletes the job; 204, or 404 if unknown |

Removed: the global `progress_data`, `GET /progress`, `GET /downloads/<name>`.

Workers:

- **Single**: calls `download_media` with the job folder as `out_dir`; a
  progress hook maps yt-dlp/normalize events to the job. On success
  `files = [main, srt?]`, status `done`. On exception status `error` with a
  short message.
- **Batch**: for each URL in order, downloads into the job folder with the
  same `dtype`, `resolution` (as a max height) and `audio_quality`; trim and
  subtitles are not offered in batch mode. A failed item is appended to
  `failed_items` and skipped. Overall percent =
  `(finished_items + current_item_percent/100) / item_count * 100`. Then
  status `zipping`, all downloaded files go into `<title>.zip` with
  `ZIP_STORED`, the loose files are deleted, `files = [zip name]`. If every
  item failed, status `error`.

Startup: `JobStore("downloads")`, `load()`, `start_cleanup_thread()`.
Existing loose files in `downloads/` (from the old layout) are removed by
`load()` only if they are older than 24 hours.

## Web page

### Mode switch

A Single / Batch segmented control at the top of the card.

- **Single**: today's flow (link, preview, Video / Audio / TikTok).
- **Batch**: the link input becomes a textarea ("one link per line, or a
  playlist link"); no preview; a live counter "N links (max 50)" counts
  pasted lines (playlist size is only known on the server). The same three
  buttons start the batch. Video in batch mode opens the resolution picker
  with fixed choices 1080 / 720 / 480 / 360, used as a maximum.

### Options panel

Collapsed "Options" toggle above the buttons. Shows only what applies:

- **Trim** (single only): Start and End text inputs, placeholder `0:00`.
  Validated on the server; errors show in the existing error line.
- **Audio quality**: chips 128 / 192 / 320 / M4A, default 192; used by Audio.
- **Subtitles** (single only, shown when the preview reports languages):
  language select + chips Off / .srt file / Burn in, default Off; used by
  Video and TikTok.

### Progress and saving

Same progress card. Batches show "3 / 12" next to the status. On `done`,
every file in `files` is saved through `/files/<job_id>/<name>`. Batch
`failed_items` are listed under the progress bar.

### History

"Recent downloads" section under the card, with the note "Files are kept
for 24 hours."

- `localStorage["video-downloader-history"]` holds up to 50 entries
  `{id, title, dtype, kind, created}`, newest first; written when a job is
  started. Reads/writes are wrapped in try/catch; without storage the
  section is simply empty.
- On page load, `POST /jobs/lookup` with the stored ids. Rows show title,
  type icon, relative time, and Save (re-downloads the files) / Delete
  (calls `DELETE /jobs/<id>`, removes the row). Unknown ids show as
  "Expired" with a remove button. Running jobs show their status.

### Icons

The remaining emojis (😢 🎉 ✅ ❌) become the same outline SVG style already
used for the buttons.

## Error handling

- Invalid form input (bad timestamps, end <= start, unknown audio quality,
  unknown subtitle mode, 0 or > 50 batch links) -> `400` with a readable
  message, no job created.
- Download failures -> job `error` with yt-dlp's message trimmed of the
  `ERROR: [site] id:` prefix.
- Server restart mid-download -> job `interrupted`, shown in history.
- Unknown / expired job ids -> 404; the page shows "Expired".
- Path traversal on `/files` is blocked by `send_from_directory`, and job
  ids must be 32 hex chars before any filesystem access.

## Testing

pytest, offline, with yt-dlp/ffmpeg/network faked:

- `jobs.py`: create/get/update/delete, `job.json` round trip, `load()`
  marks running jobs `interrupted` and drops broken folders, `cleanup()`
  removes only expired jobs, invalid ids rejected.
- `downloader.py`: `parse_timestamp`, `validate_trim`, `_format_opts` for
  each audio quality, trim and subtitle yt-dlp options,
  `get_subtitle_languages`, `expand_batch` (dedupe, non-links dropped,
  playlist expansion with a fake extractor, 50 cap).
- `app.py`: every route, single and batch workers with a fake
  `download_media` (success, failure, partial batch failure, zip contents),
  400s for invalid input, 404s for unknown jobs.

Manual check against real links before finishing: each option on its own,
`.srt` and burned Khmer subtitles, a batch and a playlist, history after a
page refresh, delete, both themes, phone width.

Existing `test_app.py` tests for the removed routes are rewritten for the
new ones.
