"""Config and GitHub client shared by the tool modules.

Both are built by init() at startup rather than at import time, so the tools
can be imported without a config file or a token present — which is what lets
the tests and CI import them at all.
"""

from .config import Config, load_config
from .github_client import GitHubClient

_cfg: Config | None = None
_gh: GitHubClient | None = None


def init() -> Config:
    global _cfg, _gh
    _cfg = load_config()
    _gh = GitHubClient(_cfg.github_token)
    return _cfg


def cfg() -> Config:
    if _cfg is None:
        raise RuntimeError("runtime.init() has not been called")
    return _cfg


def gh() -> GitHubClient:
    if _gh is None:
        raise RuntimeError("runtime.init() has not been called")
    return _gh
