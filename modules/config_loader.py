"""
Central config loader.

Every other module should pull settings through this file rather than
reading settings.yaml or .env directly — keeps config access in one place.
"""
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def load_settings() -> dict:
    """Load non-secret settings from config/settings.yaml."""
    settings_path = ROOT_DIR / "config" / "settings.yaml"
    with open(settings_path, "r") as f:
        return yaml.safe_load(f)


def get_secret(key: str) -> str:
    """Load a secret (API key, account ID, etc.) from environment / .env."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Missing required secret '{key}'. Did you copy .env.example to .env "
            f"and fill it in?"
        )
    return value


if __name__ == "__main__":
    # Quick manual check: run `python modules/config_loader.py` to confirm
    # settings.yaml parses correctly.
    settings = load_settings()
    print("Settings loaded OK:")
    print(settings)
