"""LinkedIn operation executor.

High-level capability functions backed by the operation registry,
PlaywrightLinkedinAPI client, and response parsers.
"""

import logging
import re
from typing import Optional, List

from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.operations.registry import Operation, get_operation, REGISTRY
from linkedin.operations.parsers import get_parser, ParseResult, PaginationInfo
from linkedin.operations.health import FailureType

logger = logging.getLogger(__name__)


class LinkedInOperationExecutor:
    """Executes LinkedIn Voyager operations with validation, parsing, and health checks."""

    def __init__(self, client: PlaywrightLinkedinAPI):
        """Initialize with a PlaywrightLinkedinAPI client."""
        self.client = client

    # ======================
    # Capability Functions
    # ======================

    def resolve_post_urn(self, post_url: str) -> str:
        """Extract URN from LinkedIn post URL.

        Supports:
        - https://www.linkedin.com/feed/update/urn:li:activity:123/
        - https://www.linkedin.com/posts/company_linkedin_activity-123/
        - https://www.linkedin.com/posts/urn:li:activity:123/

        Returns the URN string (e.g., 'urn:li:activity:123').
        Raises ValueError if URL cannot be parsed.
        """
        if not post_url:
            raise ValueError("post_url cannot be empty")

        # Pattern 1: Direct URN in path (urn:li:activity:...)
        urn_match = re.search(r'urn:li:(activity|share):[\w-]+', post_url)
        if urn_match:
            return urn_match.group(0)

        # Pattern 2: slug-based post URL
        # e.g., https://www.linkedin.com/posts/company_name-activityid/
        slug_match = re.search(r'linkedin\.com/posts/([\w-]+)-(\d+)', post_url)
        if slug_match:
            slug = slug_match.group(1)
            post_id = slug_match.group(2)
            # Determine URN type based on slug patterns
            # Companies often use 'companyname-activityid' format
            # Personal posts use vanity names with activity ID
            # Try to detect company vs personal from slug content
            if slug.startswith('company_') or '-' in slug and any(c.isupper() for c in slug[:10]):
                return f"urn:li:share:{post_id}"
            return f"urn:li:activity:{post_id}"

        # Pattern 3: /feed/update/ with encoded URN
        feed_match = re.search(r'feed/update/(urn%3Ali%3A[\w:%]+)', post_url)
        if feed_match:
            import urllib.parse
            return urllib.parse.unquote(feed_match.group(1))

        raise ValueError(f"Cannot extract URN from URL: {post_url}")

    def fetch_company_posts(
        self,
        company_urn_or_slug: str,
        start: int = 0,
        count: int = 10,
    ) -> ParseResult:
        """Fetch posts from a company page.

        Args:
            company_urn_or_slug: Company URN ('urn:li:company:123') or slug ('acme')
            start: Pagination offset
            count: Number of posts to fetch (max typically 50)

        Returns:
            ParseResult with list of ParsedPost objects
        """
        operation = get_operation("fetchCompanyPosts")
        if not operation:
            return self._not_captured("fetchCompanyPosts")

        # Resolve slug to numeric ID if needed
        resolved_urn = self._resolve_company_urn(company_urn_or_slug)

        variables = {
            "organizationalPageUrn": resolved_urn,
            "start": start,
            "count": count,
        }
        return self._execute_operation(operation, variables)

    def fetch_profile_posts(
        self,
        profile_urn_or_vanity: str,
        start: int = 0,
        count: int = 10,
    ) -> ParseResult:
        """Fetch posts from a personal profile.

        Args:
            profile_urn_or_vanity: Profile URN or vanity name
            start: Pagination offset
            count: Number of posts to fetch

        Returns:
            ParseResult with list of ParsedProfilePost objects
        """
        operation = get_operation("fetchProfilePosts")
        if not operation or not operation.query_id:
            return self._not_captured("fetchProfilePosts")

        variables = {
            "profileUrn": profile_urn_or_vanity,
            "start": start,
            "count": count,
        }
        return self._execute_operation(operation, variables)

    def fetch_post_detail(self, post_urn: str) -> ParseResult:
        """Fetch single post metadata.

        Note: This operation is inferred from company posts data,
        not directly captured from network traffic.

        Args:
            post_urn: Post URN (urn:li:activity:... or urn:li:share:...)

        Returns:
            ParseResult with INFERRED_FROM_COMPANY_POSTS failure
        """
        return ParseResult(
            data=[],
            pagination=PaginationInfo(has_more=False),
            failure=FailureType.INFERRED_FROM_COMPANY_POSTS,
            failure_message="fetchPostDetail is inferred from company posts data, not directly captured",
        )

    def fetch_post_comments(
        self,
        post_urn: str,
        start: int = 0,
        count: int = 10,
    ) -> ParseResult:
        """Fetch comments on a post.

        Args:
            post_urn: Post URN (urn:li:activity:... or urn:li:share:...)
            start: Pagination offset
            count: Number of comments to fetch

        Returns:
            ParseResult with list of ParsedComment objects
        """
        operation = get_operation("fetchPostComments")
        if not operation:
            return self._not_captured("fetchPostComments")

        # Convert post URN to socialDetailUrn format
        social_detail_urn = self._build_social_detail_urn(post_urn)

        variables = {
            "socialDetailUrn": social_detail_urn,
            "start": start,
            "count": count,
        }
        return self._execute_operation(operation, variables)

    def fetch_post_reactions(
        self,
        post_urn: str,
        reaction_type: Optional[str] = None,
        start: int = 0,
        count: int = 10,
    ) -> ParseResult:
        """Fetch reactions on a post.

        Args:
            post_urn: Activity URN (urn:li:activity:...)
            reaction_type: LIKE, EMPATHY, PRAISE, INTEREST, CARE, QUERY, SUPPORT, DOUBT, HELPFUL, or None for all
            start: Pagination offset
            count: Number of reactions to fetch

        Returns:
            ParseResult with list of ParsedReaction objects
        """
        operation = get_operation("fetchPostReactions")
        if not operation:
            return self._not_captured("fetchPostReactions")

        # Reactions require activity URN format
        if post_urn.startswith("urn:li:share:"):
            activity_id = post_urn.replace("urn:li:share:", "")
            post_urn = f"urn:li:activity:{activity_id}"

        variables = {
            "threadUrn": post_urn,
            "start": start,
            "count": count,
        }
        if reaction_type:
            variables["reactionType"] = reaction_type

        return self._execute_operation(operation, variables)

    def fetch_post_reposts(
        self,
        post_urn: str,
        start: int = 0,
        count: int = 100,
    ) -> ParseResult:
        """Fetch reposts of a post.

        Args:
            post_urn: Post URN
            start: Pagination offset
            count: Number of reposts to fetch (default 100)

        Returns:
            ParseResult with list of ParsedRepost objects
        """
        operation = get_operation("fetchPostReposts")
        if not operation:
            return self._not_captured("fetchPostReposts")

        # Build target URN for reposts
        # Both activity and share URNs work here
        target_urn = post_urn

        variables = {
            "targetUrn": target_urn,
            "start": start,
            "count": count,
        }
        return self._execute_operation(operation, variables)

    # ======================
    # Internal Methods
    # ======================

    def _execute_operation(
        self,
        operation: Operation,
        variables: dict,
    ) -> ParseResult:
        """Execute a single operation: build URL, call client, parse response, detect failures.

        Args:
            operation: Operation from registry
            variables: Dict of variable values

        Returns:
            ParseResult with parsed data or failure
        """
        # Build URL
        url = operation.build_url(variables)
        logger.info("Executing operation %s with variables %s", operation.name, variables)

        try:
            # Make HTTP request
            response = self.client.get(url)

            # Check for HTTP-level failures
            if response.status != 200:
                logger.warning(
                    "Operation %s returned HTTP %d",
                    operation.name,
                    response.status,
                )
                # Use health detection
                from linkedin.operations.health import detect_failure
                health = detect_failure(response.status, response)
                return ParseResult(
                    data=[],
                    pagination=PaginationInfo(has_more=False),
                    failure=health.failure_type,
                    failure_message=health.failure_message or f"HTTP {response.status}",
                )

            # Parse response body
            body_bytes = response.body()
            if not body_bytes:
                return ParseResult(
                    data=[],
                    pagination=PaginationInfo(has_more=False),
                    failure=FailureType.EMPTY_RESPONSE,
                    failure_message="Empty response body",
                )

            try:
                import json
                raw_data = json.loads(body_bytes)
            except json.JSONDecodeError as exc:
                logger.error("Failed to parse JSON from %s: %s", operation.name, exc)
                return ParseResult(
                    data=[],
                    pagination=PaginationInfo(has_more=False),
                    failure=FailureType.UNKNOWN,
                    failure_message=f"JSON parse error: {exc}",
                )

            # Get appropriate parser
            parser = get_parser(operation.name)

            # Parse response
            result = parser.parse(raw_data)

            # Log result summary
            logger.info(
                "Operation %s → %d items, has_more=%s, failure=%s",
                operation.name,
                len(result.data),
                result.pagination.has_more,
                result.failure,
            )

            return result

        except Exception as exc:
            logger.error("Operation %s raised exception: %s", operation.name, exc)
            return ParseResult(
                data=[],
                pagination=PaginationInfo(has_more=False),
                failure=FailureType.NETWORK_ERROR,
                failure_message=str(exc),
            )

    def _build_social_detail_urn(self, post_urn: str) -> str:
        """Convert post URN to socialDetailUrn format.

        LinkedIn expects: urn:li:fsd_socialDetail:(urn:li:activity:123)

        Args:
            post_urn: Post URN (urn:li:activity:... or urn:li:share:...)

        Returns:
            Social detail URN string
        """
        # Normalize share URN to activity if needed
        urn = post_urn
        if post_urn.startswith("urn:li:share:"):
            activity_id = post_urn.replace("urn:li:share:", "")
            urn = f"urn:li:activity:{activity_id}"

        return f"urn:li:fsd_socialDetail:({urn})"

    def _resolve_company_urn(self, company_urn_or_slug: str) -> str:
        """Resolve company identifier to organizationalPageUrn format.

        Handles:
        - Full URN: urn:li:company:123 -> urn:li:fsd_organizationalPage:123
        - Numeric ID: 123 -> urn:li:fsd_organizationalPage:123
        - Slug: linkedin -> urn:li:fsd_organizationalPage:1337 (via API lookup)

        Args:
            company_urn_or_slug: Company identifier

        Returns:
            organizationalPageUrn string for Voyager API
        """
        # Already a full organizationalPageUrn
        if company_urn_or_slug.startswith("urn:li:fsd_organizationalPage:"):
            return company_urn_or_slug

        # Convert urn:li:company:123 format
        if company_urn_or_slug.startswith("urn:li:company:"):
            numeric_id = company_urn_or_slug.replace("urn:li:company:", "")
            return f"urn:li:fsd_organizationalPage:{numeric_id}"

        # Pure numeric ID
        if company_urn_or_slug.isdigit():
            return f"urn:li:fsd_organizationalPage:{company_urn_or_slug}"

        # Slug - try lookup via company search API
        logger.info("Resolving company slug '%s' to numeric ID", company_urn_or_slug)
        try:
            # Use the company search endpoint
            search_url = (
                "https://www.linkedin.com/voyager/api/graphql"
                "?includeWebMetadata=true"
                "&queryId=voyagerOrganizationDashCompanies.148b1aebfadd0a455f32806df656c3c1"
                f'&variables=(keywords:{company_urn_or_slug},count:1,start:0)'
            )
            response = self.client.get(search_url)
            if response.status == 200:
                import json
                data = json.loads(response.body())
                # Extract company ID from search results
                results = data.get("data", {}).get("searchDashCompaniesByKeywords", {}).get("elements", [])
                if results:
                    company_urn = results[0].get("entityUrn", "")
                    # Extract numeric ID from urn:li:fsd_company:123
                    match = re.search(r'urn:li:fsd_company:(\d+)', company_urn)
                    if match:
                        numeric_id = match.group(1)
                        logger.info("Resolved slug '%s' -> ID %s", company_urn_or_slug, numeric_id)
                        return f"urn:li:fsd_organizationalPage:{numeric_id}"
        except Exception as exc:
            logger.warning("Company lookup failed for '%s': %s", company_urn_or_slug, exc)

        # Fallback: assume it's a slug that LinkedIn can resolve directly
        # Some endpoints accept slugs, but organizationalPageUrn requires numeric
        logger.warning(
            "Could not resolve company slug '%s' to numeric ID, "
            "passing through (may fail if endpoint requires numeric ID)",
            company_urn_or_slug,
        )
        return company_urn_or_slug

    def _not_captured(self, operation_name: str) -> ParseResult:
        """Return NEEDS_RECON for uncaptured operations."""
        return ParseResult(
            data=[],
            pagination=PaginationInfo(has_more=False),
            failure=FailureType.NEEDS_RECON,
            failure_message=f"Operation '{operation_name}' is not yet captured",
        )