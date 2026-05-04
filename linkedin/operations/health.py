"""Health checks for LinkedIn Voyager operations.

Provides typed failure detection and health monitoring for all
registered operations.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from linkedin.operations.registry import Operation, get_operation, REGISTRY


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class FailureType(Enum):
    AUTH_EXPIRED = "auth_expired"
    QUERY_ID_INVALID = "query_id_invalid"
    RESPONSE_SHAPE_CHANGED = "response_shape_changed"
    POST_UNAVAILABLE = "post_unavailable"
    PARTIAL_DATA = "partial_data"
    TEMPORARY_BLOCK = "temporary_block"
    INFERRED_FROM_COMPANY_POSTS = "inferred_from_company_posts"
    NEEDS_RECON = "needs_recon"


@dataclass
class HealthResult:
    status: HealthStatus
    failure_type: Optional[FailureType] = None
    message: str = ""
    details: dict = field(default_factory=dict)


def detect_failure(response_status: int, response_body: dict, operation: Operation) -> Optional[FailureType]:
    """Detect typed failure from response.
    
    Args:
        response_status: HTTP status code
        response_body: Parsed JSON response body
        operation: The operation that was executed
    """
    if response_status == 401:
        return FailureType.AUTH_EXPIRED
    
    if response_status == 429:
        return FailureType.TEMPORARY_BLOCK
    
    if response_status == 404:
        return FailureType.POST_UNAVAILABLE
    
    if response_status == 400:
        if response_body.get("data") is None:
            if "error" in response_body or "errors" in response_body:
                return FailureType.QUERY_ID_INVALID
        return FailureType.RESPONSE_SHAPE_CHANGED
    
    if response_status == 200:
        data = response_body.get("data", {})
        if operation.response_path:
            path_parts = operation.response_path.split(".")
            current = data
            for part in path_parts[1:]:  # Skip 'data'
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    return FailureType.RESPONSE_SHAPE_CHANGED
            
            if current is None:
                return FailureType.POST_UNAVAILABLE
            
            if isinstance(current, dict):
                elements = current.get("*elements") or current.get("elements")
                total = current.get("paging", {}).get("total")
                if elements is not None and total is not None:
                    if len(elements) < total:
                        return FailureType.PARTIAL_DATA
    
    return None


class OperationHealthChecker:
    """Checks health of LinkedIn operations and detects failure types."""
    
    def __init__(self, client):
        self.client = client
    
    def check_session(self) -> HealthResult:
        """Verify session is valid by calling /me endpoint."""
        try:
            response = self.client.get("/voyager/api/me")
            if response.status == 200:
                return HealthResult(
                    status=HealthStatus.HEALTHY,
                    message="Session is valid",
                )
            elif response.status == 401:
                return HealthResult(
                    status=HealthStatus.UNHEALTHY,
                    failure_type=FailureType.AUTH_EXPIRED,
                    message="Authentication expired",
                    details={"status": response.status},
                )
            else:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Unexpected status: {response.status}",
                    details={"status": response.status},
                )
        except Exception as e:
            return HealthResult(
                status=HealthStatus.UNHEALTHY,
                failure_type=FailureType.AUTH_EXPIRED,
                message=f"Session check failed: {e}",
            )
    
    def check_query_id(self, operation_name: str) -> HealthResult:
        """Verify a query ID is still valid by making a test request."""
        try:
            operation = get_operation(operation_name)
            if operation.status == "NEEDS_RECON":
                return HealthResult(
                    status=HealthStatus.UNKNOWN,
                    failure_type=FailureType.NEEDS_RECON,
                    message=f"Operation {operation_name} needs reconnaissance",
                )
            
            test_vars = {}
            for key, vtype in operation.variables_schema.items():
                if vtype == "int":
                    test_vars[key] = 1
                elif vtype == "str":
                    test_vars[key] = "test"
            
            url = operation.build_url(test_vars)
            response = self.client.get(url)
            
            if response.status == 200:
                return HealthResult(
                    status=HealthStatus.HEALTHY,
                    message=f"Query ID for {operation_name} is valid",
                )
            elif response.status == 400:
                body = response.json() if hasattr(response, 'json') else {}
                if body.get("data") is None and ("error" in body or "errors" in body):
                    return HealthResult(
                        status=HealthStatus.UNHEALTHY,
                        failure_type=FailureType.QUERY_ID_INVALID,
                        message=f"Query ID rejected for {operation_name}",
                        details={"status": response.status, "response": body},
                    )
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Bad request for {operation_name}",
                )
            elif response.status == 429:
                return HealthResult(
                    status=HealthStatus.UNHEALTHY,
                    failure_type=FailureType.TEMPORARY_BLOCK,
                    message="Rate limited",
                )
            else:
                return HealthResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Unexpected status {response.status} for {operation_name}",
                )
        except Exception as e:
            return HealthResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Query ID check failed: {e}",
            )
    
    def check_all_operations(self) -> dict[str, HealthResult]:
        """Run health checks on all CAPTURED operations."""
        results = {}
        for name, op in REGISTRY.items():
            if "CAPTURED" in op.status:
                results[name] = self.check_query_id(name)
            else:
                results[name] = HealthResult(
                    status=HealthStatus.UNKNOWN,
                    failure_type=FailureType.NEEDS_RECON,
                    message=f"Operation {name} not yet captured",
                )
        return results
