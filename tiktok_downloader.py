import asyncio
from TikTokApi import TikTokApi
from urllib.parse import urlparse

async def download_tiktok_video(video_url):
    try:
        # Initialize TikTokApi instance
        api = TikTokApi()

        # Parse the video URL to extract the video ID
        parsed_url = urlparse(video_url)
        path_parts = parsed_url.path.split('/')
        video_id = path_parts[-1] if path_parts[-1] else path_parts[-2]

        # Get the video object
        video = api.video(id=video_id)

        # Get the video bytes (this is an async operation)
        video_data = await video.bytes()  # Use await here

        # Save video data to a file
        video_filename = f"{video_id}.mp4"
        with open(video_filename, 'wb') as video_file:
            video_file.write(video_data)

        print(f"Video downloaded successfully: {video_filename}")

    except Exception as e:
        print(f"An error occurred: {e}")

# Get the TikTok video URL from user input
tiktok_video_url = input("Enter the TikTok video URL: ")

# Run the async function
asyncio.run(download_tiktok_video(tiktok_video_url))
