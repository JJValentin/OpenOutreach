"""LinkedIn post engagement API.

Backs the signal radar polling tasks. Uses LinkedInOperationExecutor
which is backed by the operation registry, Playwright client, and parsers.
"""

import logging
from typing import Optional

from linkedin.operations.executor import LinkedInOperationExecutor
from linkedin.operations.health import FailureType

logger = logging.getLogger(__name__)

# Legacy compatibility: these functions wrap the executor
# TODO: Migrate callers to use executor directly

def _get_executor(session):
    """Get executor from session."""
    # session may be AccountSession or have .client attribute
    client = getattr(session, 'client', session)
    return LinkedInOperationExecutor(client)

def list_own_profile_posts(session, since_days=30, limit=20):
    """Fetch authenticated user's posts from last N days."""
    executor = _get_executor(session)
    result = executor.fetch_profile_posts(
        profile_urn_or_vanity="",
        start=0,
        count=limit,
    )
    if result.failure:
        logger.warning("fetch_profile_posts failed: %s", result.failure_message)
        return []
    return [post.__dict__ for post in result.data]

def list_profile_posts(session, public_id, limit=20):
    """Fetch posts from any public profile."""
    executor = _get_executor(session)
    result = executor.fetch_profile_posts(
        profile_urn_or_vanity=public_id,
        start=0,
        count=limit,
    )
    if result.failure:
        logger.warning("fetch_profile_posts failed: %s", result.failure_message)
        return []
    return [post.__dict__ for post in result.data]

def list_company_posts(session, company_slug, limit=20):
    """Fetch posts from a company page."""
    executor = _get_executor(session)
    result = executor.fetch_company_posts(
        company_urn_or_slug=company_slug,
        start=0,
        count=limit,
    )
    if result.failure:
        logger.warning("fetch_company_posts failed: %s", result.failure_message)
        return []
    return [post.__dict__ for post in result.data]

def list_post_reactors(session, post_urn, limit=500):
    """Fetch reactors on a post."""
    executor = _get_executor(session)
    all_reactions = []
    start = 0
    count = 10

    while len(all_reactions) < limit:
        result = executor.fetch_post_reactions(
            post_urn=post_urn,
            start=start,
            count=min(count, limit - len(all_reactions)),
        )
        if result.failure:
            logger.warning("fetch_post_reactions failed: %s", result.failure_message)
            break
        all_reactions.extend(result.data)
        if not result.pagination.has_more:
            break
        start = result.pagination.next_offset or start + count

    return [r.__dict__ for r in all_reactions[:limit]]

def list_post_comments(session, post_urn, limit=500):
    """Fetch comments on a post."""
    executor = _get_executor(session)
    all_comments = []
    start = 0
    count = 10

    while len(all_comments) < limit:
        result = executor.fetch_post_comments(
            post_urn=post_urn,
            start=start,
            count=min(count, limit - len(all_comments)),
        )
        if result.failure:
            logger.warning("fetch_post_comments failed: %s", result.failure_message)
            break
        all_comments.extend(result.data)
        if not result.pagination.has_more:
            break
        start += count

    return [c.__dict__ for c in all_comments[:limit]]

def list_post_reposts(session, post_urn, limit=500):
    """Fetch reposts of a post."""
    executor = _get_executor(session)
    result = executor.fetch_post_reposts(
        post_urn=post_urn,
        start=0,
        count=min(100, limit),
    )
    if result.failure:
        logger.warning("fetch_post_reposts failed: %s", result.failure_message)
        return []
    return [r.__dict__ for r in result.data[:limit]]
