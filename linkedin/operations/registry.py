"""LinkedIn Voyager Operation Registry."""

from dataclasses import dataclass, field
from typing import Literal, Optional
from urllib.parse import quote


@dataclass(frozen=True)
class Operation:
    name: str
    query_id: str
    method: Literal["GET", "POST"]
    endpoint: str = "/voyager/api/graphql"
    variables_schema: dict = field(default_factory=dict)
    pagination_strategy: Literal["offset", "cursor", "none"] = "none"
    response_path: str = ""
    status: Literal[
        "CAPTURED",
        "CAPTURED_REQUIRES_PLAYWRIGHT_VALIDATION",
        "CAPTURED_BUT_PAGINATION_UNKNOWN",
        "CAPTURED_NEEDS_PAGINATION_AND_PARSER_TESTS",
        "NEEDS_RECON",
        "INFERRED_NOT_CAPTURED",
        "INFERRED_FROM_COMPANY_POSTS",
    ] = "NEEDS_RECON"
    notes: str = ""

    def build_url(self, variables: dict, include_web_metadata: bool = True) -> str:
        var_parts = []
        for key, value in variables.items():
            if value is None:
                continue
            if isinstance(value, bool):
                var_parts.append(f"{key}:{str(value).lower()}")
            elif isinstance(value, int):
                var_parts.append(f"{key}:{value}")
            elif isinstance(value, str):
                encoded = quote(value, safe="")
                var_parts.append(f"{key}:{encoded}")
        variables_str = f"({','.join(var_parts)})"
        params = [
            f"queryId={self.query_id}",
            f"variables={variables_str}",
        ]
        if include_web_metadata:
            params.insert(0, "includeWebMetadata=true")
        return f"https://www.linkedin.com{self.endpoint}?{'&'.join(params)}"


REGISTRY: dict[str, Operation] = {
    "fetchCompanyPosts": Operation(
        name="fetchCompanyPosts",
        query_id="voyagerFeedDashOrganizationalPageUpdates.827e11d165078dd7a5afaf1cba734121",
        method="GET",
        variables_schema={
            "count": "int",
            "start": "int",
            "moduleKey": "str",
            "organizationalPageUrn": "str",
        },
        pagination_strategy="offset",
        response_path="data.data.feedDashOrganizationalPageUpdatesByOrganizationalPageRelevanceFeed",
        status="CAPTURED_NEEDS_PAGINATION_AND_PARSER_TESTS",
        notes="Company feed listing. Uses organizationalPageUrn with numeric ID (not slug).",
    ),
    "fetchPostComments": Operation(
        name="fetchPostComments",
        query_id="voyagerSocialDashComments.afec6d88d7810d45548797a8dac4fb87",
        method="GET",
        variables_schema={
            "socialDetailUrn": "str",
            "count": "int",
            "numReplies": "int",
            "sortOrder": "str",
            "start": "int",
        },
        pagination_strategy="cursor",
        response_path="data.socialDashCommentsBySocialDetail",
        status="CAPTURED_REQUIRES_PLAYWRIGHT_VALIDATION",
        notes="Uses socialDetailUrn wrapper around activity URN. Cursor-based via paginationToken.",
    ),
    "fetchPostReactions": Operation(
        name="fetchPostReactions",
        query_id="voyagerSocialDashReactions.41ebf31a9f4c4a84e35a49d5abc9010b",
        method="GET",
        variables_schema={
            "threadUrn": "str",
            "count": "int",
            "start": "int",
            "reactionType": "str|None",
        },
        pagination_strategy="offset",
        response_path="data.data.socialDashReactionsByReactionType",
        status="CAPTURED_REQUIRES_PLAYWRIGHT_VALIDATION",
        notes="Modal has tabs (All, Like, Empathy, Praise). Each tab triggers separate request. threadUrn is activity URN.",
    ),
    "fetchPostReposts": Operation(
        name="fetchPostReposts",
        query_id="voyagerFeedDashReshareFeed.dc56f7e6b303133b71fdbb584ec2a2a5",
        method="GET",
        variables_schema={
            "targetUrn": "str",
        },
        pagination_strategy="none",
        response_path="data.reshareFeedByTargetUrn",
        status="CAPTURED_BUT_PAGINATION_UNKNOWN",
        notes="Pagination strategy unknown. Cap at 100 until validated.",
    ),
    "fetchProfilePosts": Operation(
        name="fetchProfilePosts",
        query_id="voyagerFeedDashProfileUpdates.4af00b28d60ed0f1488018948daad822",
        method="GET",
        variables_schema={
            "profileUrn": "str",
            "count": "int",
            "start": "int",
        },
        pagination_strategy="offset",
        response_path="data.posts",
        status="CAPTURED_REQUIRES_PLAYWRIGHT_VALIDATION",
        notes="Profile activity feed. Variables: profileUrn (URN or vanity), count (default 20), start (pagination offset). Requires playwright validation.",
    ),
    "fetchPostDetail": Operation(
        name="fetchPostDetail",
        query_id="",
        method="GET",
        variables_schema={},
        pagination_strategy="none",
        response_path="",
        status="INFERRED_FROM_COMPANY_POSTS",
        notes="May share queryId with fetchCompanyPosts or use separate detail query. Needs isolation test.",
    ),
    "resolvePostUrn": Operation(
        name="resolvePostUrn",
        query_id="",
        method="GET",
        variables_schema={},
        pagination_strategy="none",
        response_path="",
        status="INFERRED_NOT_CAPTURED",
        notes="URL parsing operation, no API call needed.",
    ),
}


def get_operation(name: str) -> Operation:
    if name not in REGISTRY:
        raise KeyError(f"Operation '{name}' not found in registry")
    return REGISTRY[name]


def list_operations(status_filter: Optional[str] = None) -> list[Operation]:
    ops = list(REGISTRY.values())
    if status_filter:
        ops = [op for op in ops if status_filter in op.status]
    return ops
