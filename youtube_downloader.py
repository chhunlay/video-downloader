import yt_dlp
import os

def download_video_or_audio(video_url, download_directory, download_type):
    # Create a subdirectory to store the downloaded files
    media_directory = os.path.join(download_directory, 'media')

    # Ensure the media directory exists
    if not os.path.exists(media_directory):
        os.makedirs(media_directory)

    # Specify the download options based on the user's choice
    if download_type == 'video':
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',  # Ensures downloading in MP4
            'outtmpl': os.path.join(media_directory, '%(title)s.%(ext)s'),  # Save file in the media subdirectory
        }
    elif download_type == 'audio':
        ydl_opts = {
            'format': 'bestaudio',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': os.path.join(media_directory, '%(title)s.%(ext)s'),  # Save file in the media subdirectory
        }
    else:
        print("Invalid download type selected.")
        return

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("Downloading...")
            ydl.download([video_url])
            print("Download completed!")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    url = input("Enter the YouTube video URL: ") 
    download_directory = 'Video-Downloader'  # Specify the main download directory
    download_type = input("Choose download type (video/audio): ").strip().lower()
    
    # Validate input and call the download function
    if download_type in ['video', 'audio']:
        download_video_or_audio(url, download_directory, download_type)
    else:
        print("Invalid option selected. Please enter 'video' or 'audio'.")
