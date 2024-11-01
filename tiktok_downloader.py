import requests
from TikTokApi import TikTokApi

def download_tiktok_video(video_url):
    try:
        # Create an instance of the TikTokApi
        api = TikTokApi()

        # Extract the video ID from the URL
        video_id = video_url.split('/')[-1].split('?')[0]

        # Get the video object using the video ID
        video = api.video(id=video_id)

        # Get the video bytes
        video_data = video.bytes()  # Fetch the video content

        # Write the video to a file
        video_filename = f"{video_id}.mp4"
        with open(video_filename, 'wb') as video_file:
            video_file.write(video_data)

        print(f"Video downloaded successfully: {video_filename}")

    except Exception as e:
        print(f"An error occurred: {e}")

# Get the TikTok video URL from user input
tiktok_video_url = input("Enter the TikTok video URL: ")
download_tiktok_video(tiktok_video_url)
