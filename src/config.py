import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"

load_dotenv(ROOT / ".env")


@lru_cache
def cfg() -> dict:
    with open(ROOT / "config.yaml") as f:
        c = yaml.safe_load(f)
    # Personal details come from the environment (GitHub secrets / local .env), never the public repo.
    overrides = {
        ("notify", "to"): "NOTIFY_TO",
        ("profile", "name"): "RENTER_NAME",
        ("profile", "blurb"): "RENTER_BLURB",
        ("profile", "phone"): "RENTER_PHONE",
    }
    for (section, field), var in overrides.items():
        if os.environ.get(var):
            c[section][field] = os.environ[var]
    return c


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "")
    if required and not value:
        raise RuntimeError(f"Missing required environment variable {name} (see .env.example)")
    return value
