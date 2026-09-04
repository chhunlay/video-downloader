# Building the Windows app

This turns the existing Flask + HTML app into a single double-clickable
`VideoDownloader.exe` — no terminal, no browser tab, no Python install
needed on the machine that runs it.

`desktop_app.py` is the wrapper: it starts `app.py`'s Flask server on a
free local port in a background thread, then opens it in a native window
via `pywebview`. It was smoke-tested on macOS (pywebview is
cross-platform) and confirmed to serve the page correctly — the actual
`.exe` still has to be built on Windows, since PyInstaller does not
cross-compile between operating systems.

## One-time setup (on a Windows PC)

1. Install [Python 3.11+](https://www.python.org/downloads/windows/)
   (check "Add python.exe to PATH" during install).
2. Install [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) and add its
   `bin` folder to your PATH — required for audio extraction and the
   TikTok thumbnail-embed step. Without it, video downloads still work;
   audio downloads will error.
3. Open a terminal (PowerShell or cmd) in this `web_application` folder
   and run:

   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

## Build

With the venv still active, from this folder run:

```
build_windows.bat
```

or the equivalent by hand:

```
pyinstaller --onefile --windowed --name VideoDownloader --add-data "templates;templates" desktop_app.py
```

The finished app is `dist\VideoDownloader.exe`. Copy that one file
anywhere (or send it to another Windows PC) — it's self-contained aside
from needing ffmpeg on the system PATH for audio downloads.

## Notes / limitations

- **First launch may trigger a SmartScreen warning** ("Windows protected
  your PC") since the .exe isn't code-signed. Click "More info" → "Run
  anyway". This is normal for unsigned indie builds, not a sign
  something is broken.
- Downloaded files land in a `downloads` folder created next to wherever
  the .exe is run from.
- To rebuild after editing `app.py` or `templates/index.html`, just
  re-run `build_windows.bat`.
