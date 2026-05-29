"""Cross-pipeline outreach safety helpers."""
from __future__ import annotations

import re
from typing import Iterable

from linkedin.conf import MANUAL_BLOCKED_PUBLIC_IDS


_PUBLIC_ID_RISK_TERMS = (
    "ai-automation",
    "ai_automation",
    "automation-agency",
    "automations",
    "chatbot",
    "ai-agent",
    "aiagents",
    "ai-consultant",
    "ai-consulting",
    "systems-consultant",
    "growth-automation",
    "ai-powered-marketing",
    "ai_powered_marketing",
    "ai-powered-automation",
    "ai_powered_automation",
    "ai-automation-agency",
    "ai_automation_agency",
    "ai-agency",
    "ai_agency",
    "a-i-agency",
    "ai-product",
    "ai_product",
    "workflow_automation",
    "workflow-automation",
    "ai_workflow",
    "ai-workflow",
    "ai_growth_agency",
    "ai-growth-agency",
    "ai_content_agency",
    "ai-content-agency",
)

_TEXT_RISK_PHRASES = (
    "ai automation agency",
    "ai powered automation",
    "ai-powered automation",
    "ai automation company",
    "ai automation for business",
    "ai automation for client",
    "ai automation for compan",
    "automation implementation agency",
    "ai consulting agency",
    "ai consultant",
    "ai consulting",
    "systems consultant",
    "ai systems consultant",
    "ai systems agency",
    "growth automation",
    "ai powered marketing",
    "ai chatbot",
    "ai chatbots",
    "chatbot agency",
    "ai agent builder",
    "ai agents builder",
    "ai-native agency",
    "ai native agency",
    "ai automation consultant",
    "ai automation founder",
    "builds ai pipelines",
    "builds ai automations",
    "builds automation systems",
    "builds chatbots",
    "builds ai chatbots",
    "builds cold outreach systems",
    "cold outreach systems",
    "social media dm automation",
    "directly competes",
    "vendor/competitor",
    "competitor/peer",
    "ai content agency",
    "ai content systems",
    "ai growth agency",
    "ai growth systems",
    "ai product company",
    "ai saas",
    "n8n",
    "make.com",
    "zapier automation",
    "ai workflow systems",
    "workflow automation",
    "workflow automation agency",
    "workflow automation consultant",
    "automation for coaches",
    "automation for founders",
    "ai systems for coaches",
    "ai systems for founders",
    "installs ai systems for clients",
    "install ai systems",
    "installing ai systems",
    "builds ai systems for clients",
    "build ai systems for",
    "we build ai systems",
    "sells ai systems",
    "sells automation systems",
    "peer vendor",
)

_WORD_RE = re.compile(r"[^a-z0-9]+")


def _norm(value: object) -> str:
    """Normalize text for conservative phrase matching."""
    return _WORD_RE.sub(" ", str(value or "").lower()).strip()


def _iter_text(values: Iterable[object]) -> str:
    return " ".join(_norm(v) for v in values if v)


def is_public_id_blocked(public_id: str | None) -> bool:
    """Return True when a public identifier is manually blocked.

    The block list is intentionally global and campaign-agnostic. It is used for
    competitors, peer vendors, accidental contacts, and explicit human overrides
    where no OpenOutreach campaign should interact with the profile.
    """
    if not public_id:
        return False
    return public_id.strip().lower() in MANUAL_BLOCKED_PUBLIC_IDS


def is_competitor_or_peer_risk(public_id: str | None = None, *texts: object) -> bool:
    """Return True for deterministic AI/automation competitor or peer-vendor risk.

    This is deliberately narrower than generic lead-gen/marketing/sales wording:
    those categories can be valid prospects. The hard stop is for AI/automation
    sellers, builders, consultants, systems consultants, growth automation,
    AI-powered marketing, chatbot/agent agencies, and explicit competitor/peer
    labels.
    """
    pid = (public_id or "").strip().lower()
    if pid and any(term in pid for term in _PUBLIC_ID_RISK_TERMS):
        return True

    text = _iter_text(texts)
    if not text:
        return False
    return any(_norm(phrase) in text for phrase in _TEXT_RISK_PHRASES)


def is_outreach_blocked(public_id: str | None = None, *texts: object) -> bool:
    """Return True when a profile must not receive outreach from any campaign."""
    return is_public_id_blocked(public_id) or is_competitor_or_peer_risk(public_id, *texts)


def deal_has_competitor_or_peer_risk(deal) -> bool:
    """Inspect a Deal/Lead pair for global outreach safety risk."""
    lead = getattr(deal, "lead", None)
    public_id = getattr(lead, "public_identifier", None)
    return is_outreach_blocked(
        public_id,
        getattr(deal, "reason", ""),
        getattr(deal, "profile_summary", ""),
        getattr(deal, "signal_metadata", ""),
    )
