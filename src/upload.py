import os
from pathlib import Path
from typing import List, Optional
from src.utils import logger

def get_authenticated_service(client_secrets_file: str = "client_secret.json"):
    """
    Handles OAuth 2.0 flow for YouTube Data API v3 and stores tokens in cache/token.json.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        logger.error("google-api-python-client or google-auth-oauthlib not installed.")
        raise

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
    token_file = Path("cache/youtube_token.json")
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(client_secrets_file).exists():
                raise FileNotFoundError(
                    f"Client secrets file '{client_secrets_file}' not found. Download it from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_file, "w") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)

def upload_short_to_youtube(
    video_path: Path,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    privacy_status: str = "private",
    client_secrets_file: str = "client_secret.json"
) -> str:
    """
    Uploads a video to YouTube marked as a Short.
    privacy_status defaults to 'private' to adhere to project safety specifications.
    """
    from googleapiclient.http import MediaFileUpload

    logger.info(f"Authenticating with YouTube API for uploading {video_path.name}...")
    youtube = get_authenticated_service(client_secrets_file)

    body = {
        "snippet": {
            "title": title[:100],
            "description": f"{description}\n\n#Shorts",
            "tags": tags or ["Shorts"],
            "categoryId": "28" # Science & Technology / Entertainment
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    logger.info(f"Uploading {video_path.name} to YouTube (Privacy: {privacy_status})...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info(f"Uploaded {int(status.progress() * 100)}%")

    video_id = response.get("id")
    logger.info(f"Upload complete! Video ID: {video_id}. URL: https://youtube.com/shorts/{video_id}")
    return video_id
