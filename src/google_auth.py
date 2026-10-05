from functools import lru_cache

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from src.config import env

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",  # read feed, add labels
    "https://www.googleapis.com/auth/gmail.compose",  # drafts + self-notifications (recipient-guarded)
    "https://www.googleapis.com/auth/gmail.settings.basic",  # create routing filters
]


@lru_cache
def gmail():
    creds = Credentials(
        token=None,
        refresh_token=env("GMAIL_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=env("GMAIL_CLIENT_ID"),
        client_secret=env("GMAIL_CLIENT_SECRET"),
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
