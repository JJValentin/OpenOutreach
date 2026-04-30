"""
linkedin/api/posts.py — Voyager API wrappers for Signal Radar post/engagement data.

Each function:
- Takes an AccountSession (or FakeAccountSession for tests) and limit param.
- Uses PlaywrightLinkedinAPI to call Voyager endpoints.
- Returns a list of normalised dicts.

Endpoint paths are best-effort guesses based on LinkedIn API patterns;
mark with # TODO: verify endpoint path for future confirmation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from linkedin.api.client import PlaywrightLinkedinAPI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_timestamp(raw_time: int | None) -> str:
    """Convert Voyager epoch-ms time to ISO-8601 string."""
    if raw_time is None:
        return ""
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(raw_time / 1000, tz=timezone.utc).isoformat()
    except (ValueError, OSError):
        return ""


def _build_urn_map(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build entityUrn → entity lookup from the 'included' array."""
    return {
        entity.get("entityUrn"): entity
        for entity in data.get("included", [])
        if entity.get("entityUrn")
    }


def _get_profile(entity_urn: str, urn_map: Dict[str, Any]) -> Dict[str, Any]:
    """Look up a profile entity from the urn map."""
    return urn_map.get(entity_urn, {})


def _extract_public_id(profile_entity: Dict[str, Any]) -> str:
    """Safely extract publicIdentifier from a profile entity."""
    return profile_entity.get("publicIdentifier", "") or ""


def _safe_get(data: Dict[str, Any], *keys, default: Any = None) -> Any:
    """Traverse nested dict safely."""
    for k in keys:
        if not isinstance(data, dict):
            return default
        data = data.get(k, default)
    return data


def _truncate(text: str, max_chars: int = 2000) -> str:
    """Truncate text to max_chars, preserving whole words when close to limit."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars]


def _warn_and_return_empty(msg: str, result: List) -> List:
    """Log a warning and return an empty list (malformed schema path)."""
    logger.warning(msg)
    return result


# ---------------------------------------------------------------------------
# Posts — own profile
# ---------------------------------------------------------------------------

def list_own_profile_posts(session, since_days: int = 30, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Fetch recent posts from the authenticated user's own profile.

    Args:
        session: AccountSession (or FakeAccountSession for tests)
        since_days: number of days to look back (passed as time filter)
        limit: max posts to return

    Returns:
        List[dict] with keys: post_urn, post_excerpt, published_at, author_urn
    """
    api = PlaywrightLinkedinAPI(session)
    # TODO: verify endpoint path — Voyager likely uses /me/feed/posts or similar
    url = "https://www.linkedin.com/voyager/api/feed/updates"

    from datetime import datetime, timezone, timedelta
    since_seconds = since_days * 24 * 3600
    now_ts = int(datetime.now(timezone.utc).timestamp())
    since_ts = now_ts - since_seconds

    params = {
        "count": limit,
        "start": 0,
        "q": "me",
        "timeInterval": since_ts,
        "timeGranularityType": "DAY",
    }

    try:
        res = api.get(url, params=params)
        if res.status != 200:
            return _warn_and_return_empty(
                f"list_own_profile_posts HTTP {res.status}", []
            )
        data = res.json()
    except Exception as exc:
        logger.warning("list_own_profile_posts failed: %s", exc)
        return []

    urn_map = _build_urn_map(data)
    elements = _safe_get(data, "data", "elements", default=[]) or []

    posts: List[Dict[str, Any]] = []
    for urn in elements:
        if not isinstance(urn, str):
            continue
        post_entity = urn_map.get(urn)
        if not post_entity:
            continue
        try:
            excerpt = (
                post_entity.get("subDescription", "") or
                _safe_get(post_entity, "content", "text", default="") or
                _safe_get(post_entity, "caption", default="")
            )
            raw_time = _safe_get(post_entity, "created", "time")
            author_ref = post_entity.get("author", "")
            author_profile = _get_profile(author_ref, urn_map)

            posts.append({
                "post_urn": urn,
                "post_excerpt": str(excerpt or ""),
                "published_at": _normalize_timestamp(raw_time),
                "author_urn": author_ref,
                "author_public_id": _extract_public_id(author_profile),
            })
        except Exception as exc:
            logger.debug("Skipping post %s: %s", urn, exc)
            continue

    return posts


# ---------------------------------------------------------------------------
# Posts — any public profile
# ---------------------------------------------------------------------------

def list_profile_posts(session, public_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Fetch recent posts from a public LinkedIn profile.

    Args:
        session: AccountSession
        public_id: LinkedIn public identifier (e.g. "john-doe-123")
        limit: max posts to return

    Returns:
        List[dict] with keys: post_urn, post_excerpt, published_at, author_urn
    """
    api = PlaywrightLinkedinAPI(session)
    # TODO: verify endpoint path — likely /identity/profiles/{publicId}/posts
    url = "https://www.linkedin.com/voyager/api/identity/dash/profiles"

    params = {
        "decorationId": "com.linkedin.voyager.dash.deco.identity.profile.ProfilePostsWithContext-7",
        "memberIdentity": public_id,
        "q": "memberIdentity",
        "count": limit,
        "start": 0,
    }

    try:
        res = api.get(url, params=params)
        if res.status != 200:
            return _warn_and_return_empty(
                f"list_profile_posts({public_id}) HTTP {res.status}", []
            )
        data = res.json()
    except Exception as exc:
        logger.warning("list_profile_posts(%s) failed: %s", public_id, exc)
        return []

    urn_map = _build_urn_map(data)
    elements = _safe_get(data, "data", "elements", default=[]) or []

    posts: List[Dict[str, Any]] = []
    for urn in elements:
        if not isinstance(urn, str):
            continue
        post_entity = urn_map.get(urn)
        if not post_entity:
            continue
        try:
            excerpt = (
                post_entity.get("subDescription", "") or
                _safe_get(post_entity, "content", "text", default="") or
                _safe_get(post_entity, "caption", default="")
            )
            raw_time = _safe_get(post_entity, "created", "time")
            author_ref = post_entity.get("author", "")
            author_profile = _get_profile(author_ref, urn_map)

            posts.append({
                "post_urn": urn,
                "post_excerpt": str(excerpt or ""),
                "published_at": _normalize_timestamp(raw_time),
                "author_urn": author_ref,
                "author_public_id": _extract_public_id(author_profile),
            })
        except Exception as exc:
            logger.debug("Skipping post %s: %s", urn, exc)
            continue

    return posts


# ---------------------------------------------------------------------------
# Posts — company page
# ---------------------------------------------------------------------------

def list_company_posts(session, company_slug: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Fetch recent posts from a LinkedIn company page.

    Args:
        session: AccountSession
        company_slug: company URL slug (e.g. "acme-corp")
        limit: max posts to return

    Returns:
        List[dict] with keys: post_urn, post_excerpt, published_at, author_urn
    """
    api = PlaywrightLinkedinAPI(session)
    # TODO: verify endpoint path — likely /entities/{companySlug}/posts
    url = f"https://www.linkedin.com/voyager/api/entities/companies/{company_slug}/posts"

    params = {
        "count": limit,
        "start": 0,
    }

    try:
        res = api.get(url, params=params)
        if res.status != 200:
            return _warn_and_return_empty(
                f"list_company_posts({company_slug}) HTTP {res.status}", []
            )
        data = res.json()
    except Exception as exc:
        logger.warning("list_company_posts(%s) failed: %s", company_slug, exc)
        return []

    urn_map = _build_urn_map(data)
    elements = _safe_get(data, "data", "elements", default=[]) or []

    posts: List[Dict[str, Any]] = []
    for urn in elements:
        if not isinstance(urn, str):
            continue
        post_entity = urn_map.get(urn)
        if not post_entity:
            continue
        try:
            excerpt = (
                post_entity.get("subDescription", "") or
                _safe_get(post_entity, "content", "text", default="") or
                _safe_get(post_entity, "caption", default="")
            )
            raw_time = _safe_get(post_entity, "created", "time")
            author_ref = post_entity.get("author", "")
            author_profile = _get_profile(author_ref, urn_map)

            posts.append({
                "post_urn": urn,
                "post_excerpt": str(excerpt or ""),
                "published_at": _normalize_timestamp(raw_time),
                "author_urn": author_ref,
                "author_public_id": _extract_public_id(author_profile),
            })
        except Exception as exc:
            logger.debug("Skipping post %s: %s", urn, exc)
            continue

    return posts


# ---------------------------------------------------------------------------
# Post reactions / reactors
# ---------------------------------------------------------------------------

def list_post_reactors(session, post_urn: str, limit: int = 500) -> List[Dict[str, Any]]:
    """
    Fetch the list of people who reacted to a LinkedIn post.

    Args:
        session: AccountSession
        post_urn: urn:li:fs_post:XXXXX
        limit: max reactors to return ( Voyager uses count/start pagination)

    Returns:
        List[dict] with keys: profile_urn, public_identifier, reaction_type
    """
    api = PlaywrightLinkedinAPI(session)
    # TODO: verify endpoint path — likely /socialFeed/{postUrn}/reactions
    post_id = post_urn.split(":")[-1]
    url = f"https://www.linkedin.com/voyager/api/socialFeed/updates/{post_id}/reactions"

    params = {
        "count": limit,
        "start": 0,
    }

    try:
        res = api.get(url, params=params)
        if res.status != 200:
            return _warn_and_return_empty(
                f"list_post_reactors({post_urn}) HTTP {res.status}", []
            )
        data = res.json()
    except Exception as exc:
        logger.warning("list_post_reactors(%s) failed: %s", post_urn, exc)
        return []

    urn_map = _build_urn_map(data)
    elements = _safe_get(data, "data", "elements", default=[]) or []

    reactors: List[Dict[str, Any]] = []
    for action_urn in elements:
        if not isinstance(action_urn, str):
            continue
        action_entity = urn_map.get(action_urn)
        if not action_entity:
            continue
        try:
            actor_ref = action_entity.get("actor", "")
            reaction_ref = action_entity.get("action", "")
            # reaction type is in the URN: urn:li:fs_reactionType:LIKE
            reaction_type = reaction_ref.split(":")[-1] if reaction_ref else "LIKE"
            actor_profile = _get_profile(actor_ref, urn_map)

            reactors.append({
                "profile_urn": actor_ref,
                "public_identifier": _extract_public_id(actor_profile),
                "reaction_type": reaction_type,
            })
        except Exception as exc:
            logger.debug("Skipping reactor %s: %s", action_urn, exc)
            continue

    return reactors


# ---------------------------------------------------------------------------
# Post comments
# ---------------------------------------------------------------------------

def list_post_comments(session, post_urn: str, limit: int = 500) -> List[Dict[str, Any]]:
    """
    Fetch comments on a LinkedIn post.

    Args:
        session: AccountSession
        post_urn: urn:li:fs_post:XXXXX
        limit: max comments to return ( Voyager uses count/start pagination)

    Returns:
        List[dict] with keys: profile_urn, public_identifier, comment_text
        (comment_text is truncated to 2000 chars)
    """
    api = PlaywrightLinkedinAPI(session)
    # TODO: verify endpoint path — likely /socialFeed/{postUrn}/comments
    post_id = post_urn.split(":")[-1]
    url = f"https://www.linkedin.com/voyager/api/socialFeed/updates/{post_id}/comments"

    params = {
        "count": limit,
        "start": 0,
    }

    try:
        res = api.get(url, params=params)
        if res.status != 200:
            return _warn_and_return_empty(
                f"list_post_comments({post_urn}) HTTP {res.status}", []
            )
        data = res.json()
    except Exception as exc:
        logger.warning("list_post_comments(%s) failed: %s", post_urn, exc)
        return []

    urn_map = _build_urn_map(data)
    elements = _safe_get(data, "data", "elements", default=[]) or []

    comments: List[Dict[str, Any]] = []
    for comment_urn in elements:
        if not isinstance(comment_urn, str):
            continue
        comment_entity = urn_map.get(comment_urn)
        if not comment_entity:
            continue
        try:
            actor_ref = comment_entity.get("author", "")
            raw_text = comment_entity.get("text", "")
            actor_profile = _get_profile(actor_ref, urn_map)

            comments.append({
                "profile_urn": actor_ref,
                "public_identifier": _extract_public_id(actor_profile),
                "comment_text": _truncate(str(raw_text or ""), max_chars=2000),
            })
        except Exception as exc:
            logger.debug("Skipping comment %s: %s", comment_urn, exc)
            continue

    return comments


# ---------------------------------------------------------------------------
# Post reposts
# ---------------------------------------------------------------------------

def list_post_reposts(session, post_urn: str, limit: int = 500) -> List[Dict[str, Any]]:
    """
    Fetch the list of people who reposted a LinkedIn post.

    Args:
        session: AccountSession
        post_urn: urn:li:fs_post:XXXXX
        limit: max reposters to return ( Voyager uses count/start pagination)

    Returns:
        List[dict] with keys: profile_urn, public_identifier
    """
    api = PlaywrightLinkedinAPI(session)
    # TODO: verify endpoint path — likely /socialFeed/{postUrn}/reposts
    post_id = post_urn.split(":")[-1]
    url = f"https://www.linkedin.com/voyager/api/socialFeed/updates/{post_id}/reposts"

    params = {
        "count": limit,
        "start": 0,
    }

    try:
        res = api.get(url, params=params)
        if res.status != 200:
            return _warn_and_return_empty(
                f"list_post_reposts({post_urn}) HTTP {res.status}", []
            )
        data = res.json()
    except Exception as exc:
        logger.warning("list_post_reposts(%s) failed: %s", post_urn, exc)
        return []

    urn_map = _build_urn_map(data)
    elements = _safe_get(data, "data", "elements", default=[]) or []

    reposters: List[Dict[str, Any]] = []
    for action_urn in elements:
        if not isinstance(action_urn, str):
            continue
        action_entity = urn_map.get(action_urn)
        if not action_entity:
            continue
        try:
            actor_ref = action_entity.get("actor", "")
            actor_profile = _get_profile(actor_ref, urn_map)

            reposters.append({
                "profile_urn": actor_ref,
                "public_identifier": _extract_public_id(actor_profile),
            })
        except Exception as exc:
            logger.debug("Skipping repost %s: %s", action_urn, exc)
            continue

    return reposters