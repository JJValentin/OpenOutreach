"""Signal Radar smoke test.

Exercises all four engagement operations against LinkedIn's API via Chrome CDP.
Usage: python manage.py smoke_test_signal_radar --target-company 1337
       python manage.py smoke_test_signal_radar --target-company 1337 --fallback-post-urn urn:li:activity:12345
Exit codes: 0=pass, 1=failure, 2=setup error
"""
import argparse
import json
import os
import sys
import requests
from playwright.sync_api import sync_playwright

# Django setup must happen before any model imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

import django
django.setup()

from dataclasses import dataclass
from typing import Optional

from linkedin.models import LinkedInProfile
from linkedin.api.client import PlaywrightLinkedinAPI
from linkedin.operations.executor import LinkedInOperationExecutor
from linkedin.operations.health import FailureType
from linkedin.operations.parsers import ParseResult


CDP_ENDPOINT = "http://localhost:9222"
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_SETUP_ERROR = 2

# Sentinel for "skip" status (operation not exercised — not a pass, not a fail)
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"


@dataclass
class OpResult:
    name: str
    status: str  # STATUS_PASS | STATUS_FAIL | STATUS_SKIP
    count: int = 0
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.status == STATUS_PASS

    @property
    def skipped(self) -> bool:
        return self.status == STATUS_SKIP

    @property
    def failed(self) -> bool:
        return self.status == STATUS_FAIL


def classify_failure(failure: Optional[FailureType]) -> str:
    """Classify a FailureType into PASS / FAIL / SKIP status.

    C1 mapping:
      None                         -> PASS
      NEEDS_RECON                  -> FAIL
      RESPONSE_SHAPE_CHANGED       -> FAIL
      POST_UNAVAILABLE             -> SKIP
      Any other failure            -> FAIL
    """
    if failure is None:
        return STATUS_PASS
    if failure == FailureType.POST_UNAVAILABLE:
        return STATUS_SKIP
    # NEEDS_RECON, RESPONSE_SHAPE_CHANGED, and all others -> FAIL
    return STATUS_FAIL


def get_chrome_ws_url() -> Optional[str]:
    """Fetch WebSocket URL from Chrome CDP endpoint."""
    try:
        resp = requests.get(f"{CDP_ENDPOINT}/json/version", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("webSocketDebuggerUrl")
    except Exception as e:
        print(f"Chrome CDP connection failed: {e}", file=sys.stderr)
    return None


def run_operation(executor: LinkedInOperationExecutor, op_name: str, fn, *args, **kwargs) -> OpResult:
    """Run a single operation and return its classified result."""
    try:
        result = fn(*args, **kwargs)
        if isinstance(result, ParseResult):
            status = classify_failure(result.failure)
            error = None
            if status == STATUS_FAIL:
                error = result.failure_message or (result.failure.value if result.failure else None)
            return OpResult(
                name=op_name,
                status=status,
                count=len(result.data),
                error=error,
            )
        else:
            return OpResult(name=op_name, status=STATUS_PASS, count=0)
    except Exception as e:
        return OpResult(name=op_name, status=STATUS_FAIL, count=0, error=str(e))


def compute_exit_code(ops_results: list) -> int:
    """Compute exit code from list of OpResult.

    C1 exit logic:
      - Any FAIL         -> exit 1
      - No FAIL, >=1 PASS -> exit 0
      - No FAIL, no PASS (all SKIP) -> exit 1 (insufficient health signal)
    """
    has_fail = any(r.failed for r in ops_results)
    has_pass = any(r.passed for r in ops_results)

    if has_fail:
        return EXIT_FAIL
    if has_pass:
        return EXIT_OK
    # All SKIPs — insufficient signal
    return EXIT_FAIL


def main(target_company: str = "1337", json_output: bool = False, fallback_post_urn: Optional[str] = None, include_profile_posts: Optional[str] = None):
    """Run the smoke test and return exit code."""
    # 1. Look up an active LinkedInProfile
    profile = LinkedInProfile.objects.filter(active=True).first()
    if not profile:
        if not json_output:
            print("ERROR: No active LinkedInProfile found", file=sys.stderr)
        return EXIT_SETUP_ERROR

    # 2. Connect to Chrome CDP
    ws_url = get_chrome_ws_url()
    if not ws_url:
        if not json_output:
            print("ERROR: Cannot connect to Chrome at localhost:9222", file=sys.stderr)
        return EXIT_SETUP_ERROR

    if not json_output:
        print(f"Signal Radar Smoke Test")
        print(f"Target company: {target_company}")
        print(f"Chrome CDP: {ws_url}")
        print(f"Profile: {profile.linkedin_username}")
        if fallback_post_urn:
            print(f"Fallback post URN: {fallback_post_urn}")
        print("---")

    try:
        # Create a minimal session-like object for PlaywrightLinkedinAPI
        # We connect directly via CDP WebSocket URL
        class MinimalSession:
            def __init__(self, profile, ws_url):
                self.linkedin_profile = profile
                self._ws_url = ws_url

        playwright = sync_playwright().start()
        # Connect to existing Chrome via CDP
        browser = playwright.chromium.connect_over_cdp(ws_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        class AccountSessionLike:
            def __init__(self, page, context, profile):
                self.page = page
                self.context = context
                self.linkedin_profile = profile

        session = AccountSessionLike(page, context, profile)
        client = PlaywrightLinkedinAPI(session)
        executor = LinkedInOperationExecutor(client)
    except Exception as e:
        if not json_output:
            print(f"ERROR: Failed to create API client: {e}", file=sys.stderr)
        return EXIT_SETUP_ERROR

    # 3. Run operations
    ops_results = []

    # fetchCompanyPosts — capture ParseResult to get first post URN
    first_post_urn = None
    zero_posts_warning = False
    try:
        posts_result = executor.fetch_company_posts(target_company, count=5)
        if isinstance(posts_result, ParseResult):
            status = classify_failure(posts_result.failure)
            error = None
            if status == STATUS_FAIL:
                error = posts_result.failure_message or (posts_result.failure.value if posts_result.failure else None)
            ops_results.append(OpResult(
                name="fetchCompanyPosts",
                status=status,
                count=len(posts_result.data),
                error=error,
            ))
            if status == STATUS_PASS and posts_result.data:
                first_post_urn = posts_result.data[0].urn
            elif status == STATUS_PASS and not posts_result.data:
                # Zero posts — C2: engagement ops become SKIP unless fallback URN provided
                zero_posts_warning = True
        else:
            ops_results.append(OpResult(name="fetchCompanyPosts", status=STATUS_PASS, count=0))
            zero_posts_warning = True
    except Exception as e:
        ops_results.append(OpResult(name="fetchCompanyPosts", status=STATUS_FAIL, count=0, error=str(e)))

    # C2: Determine the URN to use for engagement ops
    # Priority: actual post from fetchCompanyPosts > fallback_post_urn > none
    engagement_urn = first_post_urn or fallback_post_urn

    # C2: Warn when falling back or skipping
    if zero_posts_warning and not json_output:
        if fallback_post_urn:
            print(f"WARN: Test target company posted no public content; using --fallback-post-urn for engagement ops.")
        else:
            print(
                "WARN: Test target company posted no public content; engagement ops not exercised. "
                "Use --target-company <id> with active posts or --fallback-post-urn <urn> to fully validate."
            )

    # fetchPostReactions
    if engagement_urn:
        result = run_operation(
            executor, "fetchPostReactions",
            executor.fetch_post_reactions, engagement_urn, count=5
        )
        ops_results.append(result)
    else:
        # C2: no posts and no fallback — SKIP (not PASS)
        ops_results.append(OpResult(name="fetchPostReactions", status=STATUS_SKIP, count=0,
                                    error="No posts available; skipped"))

    # fetchPostComments
    if engagement_urn:
        result = run_operation(
            executor, "fetchPostComments",
            executor.fetch_post_comments, engagement_urn, count=5
        )
        ops_results.append(result)
    else:
        ops_results.append(OpResult(name="fetchPostComments", status=STATUS_SKIP, count=0,
                                    error="No posts available; skipped"))

    # fetchPostReposts
    if engagement_urn:
        result = run_operation(
            executor, "fetchPostReposts",
            executor.fetch_post_reposts, engagement_urn, count=5
        )
        ops_results.append(result)
    else:
        ops_results.append(OpResult(name="fetchPostReposts", status=STATUS_SKIP, count=0,
                                    error="No posts available; skipped"))

    # fetchProfilePosts (if requested)
    if include_profile_posts:
        result = run_operation(
            executor, "fetchProfilePosts",
            executor.fetch_profile_posts, include_profile_posts
        )
        ops_results.append(result)

    # Summary
    exit_code = compute_exit_code(ops_results)
    overall_pass = exit_code == EXIT_OK

    if json_output:
        output = {
            "overall_pass": overall_pass,
            "exit_code": exit_code,
            "operations": {
                r.name: {"status": r.status, "count": r.count, "error": r.error}
                for r in ops_results
            },
            "profile": profile.linkedin_username,
        }
        print(json.dumps(output))
    else:
        for r in ops_results:
            suffix = f" ({r.error})" if r.error else ""
            print(f"  [{r.status}] {r.name}: {r.count} items{suffix}")
        print("---")
        print(f"Overall: {'PASS' if overall_pass else 'FAIL'} (exit {exit_code})")

    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Signal Radar Smoke Test")
    parser.add_argument("--target-company", default="1337")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fallback-post-urn", default=None,
                        help="URN of a known post to use for engagement ops when target company has no posts")
    parser.add_argument("--include-profile-posts", default=None, metavar="VANITY",
                        help="Run fetchProfilePosts smoke against this profile vanity")
    args = parser.parse_args()
    sys.exit(main(
        target_company=args.target_company,
        json_output=args.json,
        fallback_post_urn=args.fallback_post_urn,
        include_profile_posts=args.include_profile_posts,
    ))
