"""One-time Gmail OAuth: opens a browser consent screen and stores the refresh token in .env.

    uv run python scripts/gmail_auth.py path/to/client_secret.json
"""

import json
import re
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.google_auth import SCOPES  # noqa: E402

ENV = Path(__file__).resolve().parent.parent / ".env"


def set_env(values: dict) -> None:
    text = ENV.read_text() if ENV.exists() else ""
    for k, v in values.items():
        line = f"{k}={v}"
        if re.search(rf"^{k}=.*$", text, flags=re.M):
            text = re.sub(rf"^{k}=.*$", line, text, flags=re.M)
        else:
            text += ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    ENV.write_text(text)
    ENV.chmod(0o600)


def main() -> None:
    import os

    from dotenv import load_dotenv

    load_dotenv(ENV)
    if len(sys.argv) > 1:
        info = json.loads(Path(sys.argv[1]).read_text())
        client = info.get("installed") or info.get("web")
    else:  # client id/secret already stored in .env
        client = {"client_id": os.environ["GMAIL_CLIENT_ID"], "client_secret": os.environ["GMAIL_CLIENT_SECRET"]}
    flow = InstalledAppFlow.from_client_config(
        {"installed": {**client, "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}},
        SCOPES,
    )
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    set_env({
        "GMAIL_CLIENT_ID": client["client_id"],
        "GMAIL_CLIENT_SECRET": client["client_secret"],
        "GMAIL_REFRESH_TOKEN": creds.refresh_token,
    })
    print("Saved Gmail credentials to .env")


if __name__ == "__main__":
    main()
