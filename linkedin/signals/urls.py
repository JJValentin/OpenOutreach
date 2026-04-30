from enum import StrEnum
from urllib.parse import urlparse


class WatchedSourceKind(StrEnum):
    OWN_PROFILE = "OWN_PROFILE"
    COMPETITOR_COMPANY = "COMPETITOR_COMPANY"
    INFLUENCER_PROFILE = "INFLUENCER_PROFILE"


def normalize_watched_source_identifier(url_or_identifier: str, kind: WatchedSourceKind) -> str:
    value = (url_or_identifier or "").strip()
    if not value:
        raise ValueError("Watched source identifier is required")
    parsed = urlparse(value if "://" in value else f"https://www.linkedin.com/{value.lstrip('/')}")
    if "linkedin.com" not in parsed.netloc:
        raise ValueError("Watched source must be a LinkedIn URL or identifier")
    parts = [p for p in parsed.path.split("/") if p]
    if kind == WatchedSourceKind.COMPETITOR_COMPANY and len(parts) >= 2 and parts[0] == "company":
        return parts[1]
    if kind in (WatchedSourceKind.OWN_PROFILE, WatchedSourceKind.INFLUENCER_PROFILE) and len(parts) >= 2 and parts[0] == "in":
        return parts[1]
    raise ValueError(f"Invalid LinkedIn URL for {kind}")