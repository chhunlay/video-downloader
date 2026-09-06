import yt_dlp
import os
from tqdm import tqdm

progress_bar = None

def progress_hook(d):
    global progress_bar

    if d['status'] == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate')
        downloaded = d.get('downloaded_bytes', 0)

        if total:
            if progress_bar is None:
                progress_bar = tqdm(
                    total=total,
                    unit='B',
                    unit_scale=True,
                    unit_divisor=1024,
                    desc='⬇️ Downloading'
                )

            progress_bar.n = downloaded
            progress_bar.refresh()

    elif d['status'] == 'finished':
        if progress_bar:
            progress_bar.close()
        print("🔄 Processing file...")

def download_video_or_audio(video_url, download_directory, download_type):
    global progress_bar
    progress_bar = None

    media_directory = os.path.join(download_directory, 'media')
    os.makedirs(media_directory, exist_ok=True)

    base_opts = {
        'outtmpl': os.path.join(media_directory, '%(title)s.%(ext)s'),
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True,
    }

    if download_type in ['video', 'v']:
        ydl_opts = {
            **base_opts,
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
        }
    elif download_type in ['audio', 'a']:
        ydl_opts = {
            **base_opts,
            'format': 'bestaudio',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
    else:
        print("❌ Invalid download type.")
        return

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("⬇️ Starting download...")
            ydl.download([video_url])
            print("✅ Download completed!\n")
    except Exception as e:
        if progress_bar:
            progress_bar.close()
        print(f"\n❌ Error: {e}\n")


if __name__ == "__main__":
    download_directory = 'Video-Downloader'

    download_type = input("Choose download type (video/audio): ").strip().lower()
    if download_type not in ['video', 'v', 'audio', 'a']:
        print("❌ Invalid option. Exit.")
        exit()

    while True:
        url = input("Enter YouTube URL (or type 'q' to quit): ").strip()

        if url.lower() == 'q':
            print("👋 Exit downloader.")
            break

        if not url:
            print("⚠️ URL cannot be empty.\n")
            continue

        download_video_or_audio(url, download_directory, download_type)
