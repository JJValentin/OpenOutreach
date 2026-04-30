"""Signal context builder for follow-up agent prompts.

Builds a concise human-readable block describing warm signals (engagements)
from the deal's signal_metadata, for insertion into the follow-up prompt.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crm.models import Deal


_SIGNAL_BLOCK_TEMPLATE = """Warm signal context:
- Source: {source_name} ({source_kind})
- Engagement: {engagement_type} on {event_timestamp}
- Post: {post_excerpt}
{comment_line}

Use this only if it feels natural. Do not falsely imply familiarity or private access."""


def build_signal_context(deal: "Deal") -> str | None:
    """Build a warm-signal context block from deal.signal_metadata.

    Args:
        deal: CRM Deal instance with a signal_metadata JSONField.

    Returns:
        A formatted signal context string, or None if there are no signals.
    """
    metadata = deal.signal_metadata
    if not metadata:
        return None

    signals = metadata.get("signals", [])
    if not signals:
        return None

    # Take at most top 3 signals (metadata builder already sorts by recency)
    top_signals = signals[:3]

    rendered_signals = []
    for sig in top_signals:
        comment_text = sig.get("comment_text", "") or ""
        if comment_text:
            comment_line = f"- Comment: {comment_text}"
        else:
            comment_line = ""

        block = _SIGNAL_BLOCK_TEMPLATE.format(
            source_name=sig.get("source_name", "Unknown"),
            source_kind=sig.get("source_kind", "unknown"),
            engagement_type=sig.get("engagement_type", "engaged"),
            event_timestamp=sig.get("timestamp", "unknown date"),
            post_excerpt=sig.get("post_excerpt", "(no excerpt)"),
            comment_line=comment_line,
        )
        rendered_signals.append(block)

    return "\n".join(rendered_signals)