"""
Recompute composite_signal_score for all signal-sourced Deals.

Iterates Deal.objects.filter(signal_sourced=True), finds signals from past 30 days,
and updates deal.composite_signal_score.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from crm.models import Deal
from linkedin.models import Signal
from linkedin.signals.metadata import build_signal_metadata

logger = logging.getLogger(__name__)


def handle_recompute_signal_scores(task, session, qualifiers: dict) -> None:
    """Recompute composite_signal_score for all signal-sourced Deals."""
    thirty_days_ago = timezone.now() - timedelta(days=30)

    deal: Deal
    for deal in Deal.objects.filter(signal_sourced=True).select_related("campaign"):
        signals = Signal.objects.filter(
            campaign=deal.campaign,
            profile_urn=deal.lead.urn,
            created_at__gte=thirty_days_ago,
        )
        if signals.exists():
            metadata = build_signal_metadata(signals)
            deal.composite_signal_score = metadata["composite_score"]
            deal.save(update_fields=["composite_signal_score"])
            logger.info(
                "Recomputed score for deal %d: %d",
                deal.id, deal.composite_signal_score,
            )
