import pytest
from linkedin.signals.urls import normalize_watched_source_identifier, WatchedSourceKind


def test_normalizes_linkedin_company_url():
    result = normalize_watched_source_identifier(
        "https://www.linkedin.com/company/acme-inc/",
        WatchedSourceKind.COMPETITOR_COMPANY,
    )
    assert result == "acme-inc"


def test_normalizes_linkedin_profile_url():
    result = normalize_watched_source_identifier(
        "https://www.linkedin.com/in/jane-founder/",
        WatchedSourceKind.INFLUENCER_PROFILE,
    )
    assert result == "jane-founder"


def test_rejects_non_linkedin_url():
    with pytest.raises(ValueError, match="LinkedIn"):
        normalize_watched_source_identifier(
            "https://twitter.com/acme",
            WatchedSourceKind.COMPETITOR_COMPANY,
        )


def test_rejects_kind_path_mismatch():
    with pytest.raises(ValueError, match="Invalid LinkedIn URL"):
        normalize_watched_source_identifier(
            "https://www.linkedin.com/company/acme-inc/",
            WatchedSourceKind.INFLUENCER_PROFILE,
        )


def test_rejects_blank_identifier():
    with pytest.raises(ValueError, match="required"):
        normalize_watched_source_identifier("", WatchedSourceKind.OWN_PROFILE)