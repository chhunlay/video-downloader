import yt_dlp
import os

def download_video(video_url, download_directory):
    # Create a subdirectory to store the downloaded videos
    videos_directory = os.path.join(download_directory, 'videos')
    
    # Ensure the videos directory exists
    if not os.path.exists(videos_directory):
        os.makedirs(videos_directory)

    # Specify the download options
    ydl_opts = {
        'format': 'best',
        'outtmpl': os.path.join(videos_directory, '%(title)s.%(ext)s'),  # Save file in the videos subdirectory
    }

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
    download_video(url, download_directory)
import yt_dlp
import os

def download_video(video_url, download_directory):
    # Create a subdirectory to store the downloaded videos
    videos_directory = os.path.join(download_directory, 'videos')
    
    # Ensure the videos directory exists
    if not os.path.exists(videos_directory):
        os.makedirs(videos_directory)

    # Specify the download options
    ydl_opts = {
        'format': 'best',
        'outtmpl': os.path.join(videos_directory, '%(title)s.%(ext)s'),  # Save file in the videos subdirectory
    }

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
    download_video(url, download_directory)
