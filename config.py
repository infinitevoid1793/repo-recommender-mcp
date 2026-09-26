"""Loads config.yaml and the GitHub token once at server startup."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

CONFIG_PATH = Path(__file__).parent / "config.yaml"
ENV_PATH = Path(__file__).parent / ".env"


class Config:
    def __init__(self, github_token: str, seed_repos: list[str], profile_blurb: str):
        self.github_token = github_token
        self.seed_repos = seed_repos
        self.profile_blurb = profile_blurb


def _flatten_seed_repos(raw: dict | list | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    flat: list[str] = []
    for repos in raw.values():
        flat.extend(repos or [])
    return flat


def load_config() -> Config:
    load_dotenv(ENV_PATH)

    if not CONFIG_PATH.exists():
        raise RuntimeError(
            f"Missing {CONFIG_PATH}. Copy config.example.yaml to config.yaml and fill it in."
        )

    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f) or {}

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing GITHUB_TOKEN. Copy .env.example to .env and paste your token in."
        )

    seed_repos = _flatten_seed_repos(raw.get("seed_repos"))
    profile_blurb = raw.get("profile_blurb", "").strip()

    return Config(github_token=token, seed_repos=seed_repos, profile_blurb=profile_blurb)
