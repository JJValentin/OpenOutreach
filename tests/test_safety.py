"""Tests for deterministic outreach safety helpers."""
from __future__ import annotations

import pytest

from linkedin.safety import is_competitor_or_peer_risk


@pytest.mark.parametrize(
    ("public_id", "profile_text"),
    [
        (
            "yaakov-oranski",
            "Yotomations helps businesses with AI-powered automation, CRM, n8n workflow automation, and GoHighLevel.",
        ),
        (
            "vered-ben-dor",
            "AIFlow Solution builds AI chatbots, workflow automation, and virtual assistants for SMBs.",
        ),
        (
            "gijueldhose",
            "Founder at zcalo.ai, an AI product company building software for businesses.",
        ),
        (
            "markvange",
            "Founder of Autom8ly LLC, an AI automation agency for service businesses.",
        ),
        (
            "a-i-agency-171a28395",
            "Founder helping companies improve operations.",
        ),
        (
            "elbahwi-haroune",
            "Co-founder of Hinet Agency who also builds AI SaaS products for business automation.",
        ),
        (
            "ihab-khoudari-7a9aa917b",
            "Co-founder of Hinet Agency with a secondary AI SaaS offer for clients.",
        ),
    ],
)
def test_competitor_or_peer_risk_catches_known_competitors(public_id, profile_text):
    assert is_competitor_or_peer_risk(public_id, profile_text) is True


@pytest.mark.parametrize(
    ("public_id", "profile_text"),
    [
        (
            "fitness-coach-alice",
            "Fitness coach who wants more clients and runs online coaching programs for busy professionals.",
        ),
        (
            "b2b-saas-sales-founder",
            "B2B SaaS founder and VP Sales focused on pipeline generation, demos, and revenue operations.",
        ),
    ],
)
def test_competitor_or_peer_risk_allows_legitimate_icp(public_id, profile_text):
    assert is_competitor_or_peer_risk(public_id, profile_text) is False
