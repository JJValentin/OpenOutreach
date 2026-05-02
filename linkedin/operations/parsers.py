"""LinkedIn Voyager response parsers.

Each parser takes the raw GraphQL response dict and returns a typed dataclass.
Handles RESPONSE_SHAPE_CHANGED gracefully by checking expected paths.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Any, Dict
from datetime import datetime
import json

from linkedin.operations.registry import Operation, get_operation
from linkedin.operations.health import FailureType, detect_failure


# ======================
# Dataclasses
# ======================

@dataclass
class ParsedPost:
    """A single LinkedIn post."""
    urn: str
    author_urn: str
    text: str
    published_at: Optional[datetime]
    reaction_count: int
    comment_count: int
    repost_count: int
    url: Optional[str]


@dataclass
class ParsedComment:
    """A single comment on a post."""
    urn: str
    author_urn: str
    text: str
    created_at: Optional[datetime]
    reply_count: int
    reactions: Dict[str, int]  # reaction type -> count


@dataclass
class ParsedReaction:
    """A single reaction to a post."""
    actor_urn: str
    actor_name: str
    reaction_type: str  # LIKE, EMPATHY, PRAISE, INTEREST, CARE, QUERY, SUPPORT, DOUBT, HELPFUL
    timestamp: Optional[datetime]


@dataclass
class ParsedRepost:
    """A single repost/reshare of a post."""
    actor_urn: str
    actor_name: str
    text: Optional[str]
    timestamp: Optional[datetime]


@dataclass
class PaginationInfo:
    """Pagination metadata from LinkedIn responses."""
    has_more: bool
    next_cursor: Optional[str] = None  # cursor-based pagination
    next_offset: Optional[int] = None  # offset-based pagination
    total: Optional[int] = None


@dataclass
class ParseResult:
    """Result of parsing a LinkedIn operation response."""
    data: List[Any]  # List[ParsedPost | ParsedComment | ParsedReaction | ParsedRepost]
    pagination: PaginationInfo
    failure: Optional[FailureType] = None
    failure_message: str = ""
    raw: dict = field(default_factory=dict, repr=False)  # original response for debugging


# ======================
# Timestamp helpers
# ======================

def _parse_linkedin_timestamp(epoch_ms: Optional[int]) -> Optional[datetime]:
    """Parse LinkedIn's epoch-ms timestamp to datetime."""
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000.0)


# ======================
# Base Parser
# ======================

class BaseParser:
    """Base parser with common LinkedIn response handling."""

    operation_name: str = ""

    def parse(self, raw_response: dict) -> ParseResult:
        """Parse a raw response. Returns ParseResult with failure detection."""
        raise NotImplementedError

    def _extract_pagination(self, data: dict) -> PaginationInfo:
        """Extract pagination info from response data dict.

        Subclasses should override to extract operation-specific pagination.
        Default tries common patterns: pagingInfo, paginationToken, hasMore.
        """
        # Default: try pagingInfo path
        paging = data.get("pagingInfo") or data.get("paging") or {}
        if paging:
            return PaginationInfo(
                has_more=bool(paging.get("hasMore", False)),
                next_offset=paging.get("start", None) + paging.get("count", 0) if paging.get("hasMore") else None,
                total=paging.get("total"),
            )

        # Try top-level has_more
        if "hasMore" in data:
            return PaginationInfo(
                has_more=bool(data.get("hasMore")),
                next_offset=data.get("nextStart") or data.get("start", 0) + data.get("count", 0),
                total=data.get("total"),
            )

        return PaginationInfo(has_more=False)

    def _resolve_references(self, raw: dict, elements: List[str]) -> List[dict]:
        """Resolve URN references from '*elements' array using the 'included' array.

        LinkedIn uses URN references (*elements) that need resolution from 'included' array.
        Returns list of resolved entity dicts.
        """
        included = raw.get("included", [])
        urn_map = {
            entity.get("entityUrn"): entity
            for entity in included
            if entity.get("entityUrn")
        }
        return [urn_map.get(urn) for urn in elements if urn_map.get(urn)]


# ======================
# Company Posts Parser
# ======================

class CompanyPostsParser(BaseParser):
    """Parser for fetchCompanyPosts operation."""

    operation_name = "fetchCompanyPosts"

    def parse(self, raw_response: dict) -> ParseResult:
        """Extract posts from organizationPageUpdateV2."""
        try:
            # Walk expected path
            data = raw_response.get("data", {})
            if not data:
                return self._shape_changed(raw_response, "missing data")

            page_update = data.get("organizationPageUpdateV2")
            if not page_update:
                return self._shape_changed(raw_response, "missing organizationPageUpdateV2")

            elements = page_update.get("elements", [])
            if not elements:
                return ParseResult(
                    data=[],
                    pagination=self._extract_pagination(page_update),
                    raw=raw_response,
                )

            # Resolve URN references
            resolved = self._resolve_references(raw_response, elements)
            posts = []
            for entity in resolved:
                if not entity:
                    continue

                # Extract social detail for engagement counts
                social_detail = entity.get("socialDetail", {})
                if isinstance(social_detail, dict):
                    counts = social_detail.get("totalSocialActivitiesByEntity", {})
                else:
                    counts = {}

                # Get text content
                content = entity.get("content", {})
                text = ""
                if isinstance(content, dict):
                    text = content.get("text", "")
                elif isinstance(content, str):
                    text = content

                # Get author URN
                author_urn = entity.get("author", "") or entity.get("authorUrn", "")

                # Get timestamp
                created = entity.get("created", {})
                published_at = None
                if isinstance(created, dict):
                    published_at = _parse_linkedin_timestamp(created.get("time"))

                # Build post URL
                urn = entity.get("entityUrn", "")
                url = None
                if urn:
                    url = f"https://www.linkedin.com/feed/update/{urn}/"

                # Extract engagement counts
                reaction_count = counts.get("reactionCount", 0)
                comment_count = counts.get("commentCount", 0)
                repost_count = counts.get("reshareCount", 0)

                posts.append(ParsedPost(
                    urn=urn,
                    author_urn=author_urn,
                    text=text,
                    published_at=published_at,
                    reaction_count=reaction_count,
                    comment_count=comment_count,
                    repost_count=repost_count,
                    url=url,
                ))

            pagination = self._extract_pagination(page_update)
            return ParseResult(data=posts, pagination=pagination, raw=raw_response)

        except Exception as exc:
            logger = _get_logger()
            logger.warning("CompanyPostsParser failed: %s", exc)
            return ParseResult(
                data=[],
                pagination=PaginationInfo(has_more=False),
                failure=FailureType.PARTIAL_DATA,
                failure_message=str(exc),
                raw=raw_response,
            )

    def _shape_changed(self, raw: dict, reason: str) -> ParseResult:
        """Return RESPONSE_SHAPE_CHANGED failure."""
        return ParseResult(
            data=[],
            pagination=PaginationInfo(has_more=False),
            failure=FailureType.RESPONSE_SHAPE_CHANGED,
            failure_message=f"Response shape changed: {reason}",
            raw=raw,
        )


# ======================
# Comments Parser
# ======================

class CommentsParser(BaseParser):
    """Parser for fetchPostComments operation."""

    operation_name = "fetchPostComments"

    def parse(self, raw_response: dict) -> ParseResult:
        """Extract comments from socialDashCommentsBySocialDetail."""
        try:
            data = raw_response.get("data", {})
            if not data:
                return self._shape_changed(raw_response, "missing data")

            inner_data = data.get("data", {})
            comments_container = inner_data.get("socialDashCommentsBySocialDetail")
            if not comments_container:
                return self._shape_changed(raw_response, "missing socialDashCommentsBySocialDetail")

            # Use *elements (URNs) - resolve actual data from included array
            element_urns = comments_container.get("*elements", [])
            if not element_urns:
                return ParseResult(
                    data=[],
                    pagination=self._extract_pagination(comments_container),
                    raw=raw_response,
                )

            # Build lookup from included array for comment entities
            included = raw_response.get("included", [])
            included_by_urn = {}
            for item in included:
                if item.get("$type") == "com.linkedin.voyager.dash.social.Comment":
                    entity_urn = item.get("entityUrn", "")
                    if entity_urn:
                        included_by_urn[entity_urn] = item

            comments = []
            for urn in element_urns:
                if not urn:
                    continue

                # Look up full comment data from included
                entity = included_by_urn.get(urn)
                if not entity:
                    # Fallback: treat urn as entity itself
                    entity = {"entityUrn": urn}

                # Get comment URN
                comment_urn = entity.get("entityUrn", "")

                # Get author
                author_urn = entity.get("actorUrn", "") or entity.get("authorUrn", "")

                # Get text
                content = entity.get("content", {})
                text = ""
                if isinstance(content, dict):
                    text = content.get("text", "")

                # Get timestamp
                created = entity.get("created", {})
                created_at = None
                if isinstance(created, dict):
                    created_at = _parse_linkedin_timestamp(created.get("time"))

                # Get reply count
                reply_count = entity.get("replyCount", 0)

                # Get reactions breakdown
                reactions = {}
                reaction_meta = entity.get("reactionTypeCounts", []) or entity.get("reactions", {})
                if isinstance(reaction_meta, list):
                    for r in reaction_meta:
                        if isinstance(r, dict):
                            rt = r.get("reactionType", "LIKE")
                            cnt = r.get("count", 0)
                            reactions[rt] = cnt
                elif isinstance(reaction_meta, dict):
                    for rt, cnt in reaction_meta.items():
                        reactions[rt] = cnt

                comments.append(ParsedComment(
                    urn=comment_urn,
                    author_urn=author_urn,
                    text=text,
                    created_at=created_at,
                    reply_count=reply_count,
                    reactions=reactions,
                ))

            pagination = self._extract_pagination(comments_container)
            return ParseResult(data=comments, pagination=pagination, raw=raw_response)

        except Exception as exc:
            logger = _get_logger()
            logger.warning("CommentsParser failed: %s", exc)
            return ParseResult(
                data=[],
                pagination=PaginationInfo(has_more=False),
                failure=FailureType.PARTIAL_DATA,
                failure_message=str(exc),
                raw=raw_response,
            )

    def _shape_changed(self, raw: dict, reason: str) -> ParseResult:
        """Return RESPONSE_SHAPE_CHANGED failure."""
        return ParseResult(
            data=[],
            pagination=PaginationInfo(has_more=False),
            failure=FailureType.RESPONSE_SHAPE_CHANGED,
            failure_message=f"Response shape changed: {reason}",
            raw=raw,
        )


# ======================
# Reactions Parser
# ======================

class ReactionsParser(BaseParser):
    """Parser for fetchPostReactions operation."""

    operation_name = "fetchPostReactions"

    def parse(self, raw_response: dict) -> ParseResult:
        """Extract reactions from socialDashReactionsByReactionType."""
        try:
            data = raw_response.get("data", {})
            if not data:
                return self._shape_changed(raw_response, "missing data")

            inner_data = data.get("data", {})
            reactions_container = inner_data.get("socialDashReactionsByReactionType")
            if not reactions_container:
                return self._shape_changed(raw_response, "missing socialDashReactionsByReactionType")

            # Use *elements (URNs) - resolve actual data from included array
            element_urns = reactions_container.get("*elements", [])
            if not element_urns:
                return ParseResult(
                    data=[],
                    pagination=self._extract_pagination(reactions_container),
                    raw=raw_response,
                )

            # Build lookup from included array for reaction entities
            included = raw_response.get("included", [])
            included_by_urn = {}
            for item in included:
                if item.get("$type") == "com.linkedin.voyager.dash.social.Reaction":
                    entity_urn = item.get("entityUrn", "")
                    if entity_urn:
                        included_by_urn[entity_urn] = item

            reactions = []
            for urn in element_urns:
                if not urn:
                    continue

                # Look up full reaction data from included
                entity = included_by_urn.get(urn)
                if not entity:
                    # Fallback: treat urn as entity itself
                    entity = {"actorUrn": urn, "reactionType": "LIKE"}

                # Get actor info
                actor_urn = entity.get("actorUrn", "")

                # Get actor name from included using actor urn
                actor_name = self._resolve_actor_name(raw_response, actor_urn)

                # Get reaction type
                reaction_type = entity.get("reactionType", "LIKE")

                # Get timestamp
                created = entity.get("created", {})
                timestamp = None
                if isinstance(created, dict):
                    timestamp = _parse_linkedin_timestamp(created.get("time"))

                reactions.append(ParsedReaction(
                    actor_urn=actor_urn,
                    actor_name=actor_name,
                    reaction_type=reaction_type,
                    timestamp=timestamp,
                ))

            pagination = self._extract_pagination(reactions_container)
            return ParseResult(data=reactions, pagination=pagination, raw=raw_response)

        except Exception as exc:
            logger = _get_logger()
            logger.warning("ReactionsParser failed: %s", exc)
            return ParseResult(
                data=[],
                pagination=PaginationInfo(has_more=False),
                failure=FailureType.PARTIAL_DATA,
                failure_message=str(exc),
                raw=raw_response,
            )

    def _resolve_actor_name(self, raw: dict, actor_urn: str) -> str:
        """Resolve actor name from 'included' array."""
        included = raw.get("included", [])
        for entity in included:
            if entity.get("entityUrn") == actor_urn:
                name = entity.get("fullName") or entity.get("name") or entity.get("actorName", "")
                return name
        return "Unknown"

    def _shape_changed(self, raw: dict, reason: str) -> ParseResult:
        """Return RESPONSE_SHAPE_CHANGED failure."""
        return ParseResult(
            data=[],
            pagination=PaginationInfo(has_more=False),
            failure=FailureType.RESPONSE_SHAPE_CHANGED,
            failure_message=f"Response shape changed: {reason}",
            raw=raw,
        )


# ======================
# Reposts Parser
# ======================

class RepostsParser(BaseParser):
    """Parser for fetchPostReposts operation."""

    operation_name = "fetchPostReposts"

    def parse(self, raw_response: dict) -> ParseResult:
        """Extract reposts from feedDashReshareFeedByReshareFeed."""
        try:
            data = raw_response.get("data", {})
            if not data:
                return self._shape_changed(raw_response, "missing data")

            # LinkedIn wraps response in data.data.feedDashReshareFeedByReshareFeed
            inner_data = data.get("data", {})
            reposts_container = inner_data.get("feedDashReshareFeedByReshareFeed")
            if not reposts_container:
                return self._shape_changed(raw_response, "missing feedDashReshareFeedByReshareFeed")

            # Use *elements (URNs) - resolve actual data from included array
            element_urns = reposts_container.get("*elements", [])
            if not element_urns:
                return ParseResult(
                    data=[],
                    pagination=self._extract_pagination(reposts_container),
                    raw=raw_response,
                )

            # Build lookup from included array for repost entities
            included = raw_response.get("included", [])
            included_by_urn = {}
            for item in included:
                if item.get("$type") == "com.linkedin.voyager.dash.social.SocialActivity":
                    entity_urn = item.get("entityUrn", "")
                    if entity_urn:
                        included_by_urn[entity_urn] = item

            reposts = []
            for urn in element_urns:
                if not urn:
                    continue

                # Look up full repost data from included
                entity = included_by_urn.get(urn)
                if not entity:
                    # Fallback: treat urn as entity itself
                    entity = {"actorUrn": urn}

                # Get actor info
                actor_urn = entity.get("actorUrn", "") or entity.get("authorUrn", "")

                # Get actor name from included
                actor_name = self._resolve_actor_name(raw_response, actor_urn)

                # Get text (share text if any)
                content = entity.get("content", {})
                text = None
                if isinstance(content, dict):
                    text = content.get("text")

                # Get timestamp
                created = entity.get("created", {})
                timestamp = None
                if isinstance(created, dict):
                    timestamp = _parse_linkedin_timestamp(created.get("time"))

                reposts.append(ParsedRepost(
                    actor_urn=actor_urn,
                    actor_name=actor_name,
                    text=text,
                    timestamp=timestamp,
                ))

            pagination = self._extract_pagination(reposts_container)
            return ParseResult(data=reposts, pagination=pagination, raw=raw_response)

        except Exception as exc:
            logger = _get_logger()
            logger.warning("RepostsParser failed: %s", exc)
            return ParseResult(
                data=[],
                pagination=PaginationInfo(has_more=False),
                failure=FailureType.PARTIAL_DATA,
                failure_message=str(exc),
                raw=raw_response,
            )

    def _resolve_actor_name(self, raw: dict, actor_urn: str) -> str:
        """Resolve actor name from 'included' array."""
        included = raw.get("included", [])
        for entity in included:
            if entity.get("entityUrn") == actor_urn:
                name = entity.get("fullName") or entity.get("name") or entity.get("actorName", "")
                return name
        return "Unknown"

    def _shape_changed(self, raw: dict, reason: str) -> ParseResult:
        """Return RESPONSE_SHAPE_CHANGED failure."""
        return ParseResult(
            data=[],
            pagination=PaginationInfo(has_more=False),
            failure=FailureType.RESPONSE_SHAPE_CHANGED,
            failure_message=f"Response shape changed: {reason}",
            raw=raw,
        )


# ======================
# Parser Registry
# ======================

PARSERS = {
    "fetchCompanyPosts": CompanyPostsParser,
    "fetchPostComments": CommentsParser,
    "fetchPostReactions": ReactionsParser,
    "fetchPostReposts": RepostsParser,
}


def get_parser(operation_name: str) -> BaseParser:
    """Get parser instance for an operation."""
    parser_cls = PARSERS.get(operation_name)
    if parser_cls:
        return parser_cls()
    return BaseParser()


def _get_logger():
    """Get logger for this module (lazy import to avoid circular deps)."""
    import logging
    return logging.getLogger(__name__)