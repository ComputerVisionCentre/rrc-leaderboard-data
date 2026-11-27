"""Mirror of public Robust Reading Competition leaderboards, plus a small loader."""

from .client import DATA_URL, competitions, load, meta, state_at

__all__ = ["DATA_URL", "competitions", "load", "meta", "state_at"]
__version__ = "0.1.0"
