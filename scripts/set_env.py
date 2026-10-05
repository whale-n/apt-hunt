"""Securely store a secret in .env (input is hidden, value never printed).

    uv run python scripts/set_env.py ANTHROPIC_API_KEY
"""

import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gmail_auth import set_env  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: set_env.py NAME")
    name = sys.argv[1]
    value = getpass(f"Paste {name} (input hidden), then press Enter: ").strip()
    if not value:
        sys.exit("Nothing entered; .env unchanged.")
    set_env({name: value})
    print(f"Saved {name} to .env ({len(value)} chars)")


if __name__ == "__main__":
    main()
