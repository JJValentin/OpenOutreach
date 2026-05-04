"""Django management command: smoke_test_signal_radar.

Usage: python manage.py smoke_test_signal_radar [--target-company 1337] [--json] [--fallback-post-urn URN]
"""
from django.core.management.base import BaseCommand
from linkedin.scripts.smoke_test import main as run_smoke_test
import sys


class Command(BaseCommand):
    help = "Run Signal Radar smoke test against LinkedIn's API via Chrome CDP"

    def add_arguments(self, parser):
        parser.add_argument(
            "--target-company",
            default="1337",
            help="LinkedIn company ID or slug"
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output JSON"
        )
        parser.add_argument(
            "--fallback-post-urn",
            default=None,
            dest="fallback_post_urn",
            help="URN of a known post to use for engagement ops when target company has no posts"
        )
        parser.add_argument(
            "--include-profile-posts",
            metavar="VANITY",
            help="Run fetchProfilePosts smoke against this profile vanity (e.g. joshuajvalentin)",
            default=None,
            dest="include_profile_posts",
        )

    def handle(self, *args, **options):
        exit_code = run_smoke_test(
            target_company=options["target_company"],
            json_output=options["json_output"],
            fallback_post_urn=options["fallback_post_urn"],
            include_profile_posts=options["include_profile_posts"],
        )
        if exit_code != 0:
            sys.exit(exit_code)
