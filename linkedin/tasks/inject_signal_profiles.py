"""inject_signal_profiles task — build Lead/Deal rows from engagement signals."""
from __future__ import annotations

import logging
from collections import defaultdict

from django.db.models import Q

from crm.models import Deal, Lead
from linkedin.conf import SIGNAL_PRIORITY_THRESHOLD
from linkedin.models import Campaign, Signal
from linkedin.signals.metadata import update_deal_signal_metadata

logger = logging.getLogger(__name__)


def handle_inject_signal_profiles(campaign: Campaign) -> None:
    """Build Lead + Deal rows from engagement signals for a campaign.

    For each Signal row with score >= SIGNAL_PRIORITY_THRESHOLD belonging
    to the campaign, grouped by profile_urn:

    1. Find or create a Lead (urn=profile_urn). Skip if disqualified.
    2. Find or create a Deal(lead, campaign) in QUALIFIED state.
    3. Update the Deal's signal_metadata and composite_signal_score via
       ``update_deal_signal_metadata``.
    4. Set Deal.signal_sourced = True.

    Does NOT create outreach tasks (no double-trigger of on_deal_state_entered).
    Re-running is idempotent: deterministic signal_metadata, no duplicate rows.
    """
    signals = Signal.objects.filter(
        campaign=campaign,
        score__gte=SIGNAL_PRIORITY_THRESHOLD,
    ).select_related("watched_source")

    if not signals.exists():
        logger.info("No signals above threshold for campaign %s", campaign)
        return

    # Group by profile_urn
    by_profile: dict[str, list[Signal]] = defaultdict(list)
    for sig in signals:
        by_profile[sig.profile_urn].append(sig)

    created_leads = 0
    updated_deals = 0

    for profile_urn, profile_signals in by_profile.items():
        # Find or create Lead
        lead = Lead.objects.filter(urn=profile_urn).first()
        if lead is None:
            # Try to derive public_identifier from first signal's payload
            public_id = ""
            payload = profile_signals[0].payload_json or {}
            # payload may contain a public_id field from the signal scraper
            public_id = payload.get("public_identifier", "")
            lead = Lead(
                urn=profile_urn,
                public_identifier=public_id,
                linkedin_url=payload.get("linkedin_url", ""),
            )
            if not public_id:
                # TODO: scrape profile to get public_identifier
                pass
            lead.save()
            created_leads += 1
            logger.info("Created Lead %s for profile %s", lead.pk, profile_urn)
        elif lead.disqualified:
            logger.debug("Skipping disqualified lead %s for profile %s", lead.pk, profile_urn)
            continue

        # Find or create Deal in QUALIFIED state (no scheduler hook)
        deal = Deal.objects.filter(lead=lead, campaign=campaign).first()
        if deal is None:
            deal = Deal(
                lead=lead,
                campaign=campaign,
                state="QUALIFIED",  # default state, no scheduler hook called
            )
            deal.save()
            logger.info("Created Deal %s for Lead %s, campaign %s", deal.pk, lead.pk, campaign)
        else:
            logger.info("Updating existing Deal %s for profile %s", deal.pk, profile_urn)

        # Update signal metadata
        update_deal_signal_metadata(deal, profile_signals)
        updated_deals += 1

    logger.info(
        "inject_signal_profiles complete for campaign %s: %d leads created, %d deals updated",
        campaign,
        created_leads,
        updated_deals,
    )

    # Mark any INJECT_SIGNAL_PROFILES task for this campaign as COMPLETED.
    # This keeps the handler compatible with both direct calls and daemon dispatch.
    from django.utils import timezone
    from linkedin.models import Task
    Task.objects.filter(
        task_type=Task.TaskType.INJECT_SIGNAL_PROFILES,
        status=Task.Status.PENDING,
        payload__campaign_id=campaign.id,
    ).update(
        status=Task.Status.COMPLETED,
        completed_at=timezone.now(),
    )


def enqueue_inject_signal_profiles(campaign_id: int, delay_seconds: float = 10) -> None:
    """Enqueue an inject_signal_profiles task for the given campaign."""
    from linkedin.models import Task
    from django.utils import timezone
    from datetime import timedelta

    filter_kwargs = {
        "task_type": Task.TaskType.INJECT_SIGNAL_PROFILES,
        "status": Task.Status.PENDING,
        "payload__campaign_id": campaign_id,
    }

    if Task.objects.filter(**filter_kwargs).exists():
        return

    Task.objects.create(
        task_type=Task.TaskType.INJECT_SIGNAL_PROFILES,
        scheduled_at=timezone.now() + timedelta(seconds=delay_seconds),
        payload={"campaign_id": campaign_id},
    )