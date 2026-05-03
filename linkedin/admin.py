from django.contrib import admin, messages
from django import forms
from django.http import HttpResponse
from django.utils import timezone
from datetime import timedelta
import csv

from chat.models import ChatMessage

from linkedin.models import ActionLog, Campaign, LinkedInProfile, SearchKeyword, Signal, SignalRadarState, SiteConfig, Task, WatchedSource
from linkedin.signals.urls import normalize_watched_source_identifier, WatchedSourceKind
from linkedin.conf import SIGNAL_RATE_LIMIT_PAUSE_HOURS
from linkedin.tasks.scheduler import enqueue_poll_watched_source


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    list_display = ("__str__", "llm_provider", "ai_model", "llm_api_base")

    def has_add_permission(self, request):
        return not SiteConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "booking_link", "is_freemium", "action_fraction")
    filter_horizontal = ("users",)


@admin.register(LinkedInProfile)
class LinkedInProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "linkedin_username", "active", "legal_accepted")
    list_filter = ("active",)
    raw_id_fields = ("user", "self_lead")


@admin.register(SearchKeyword)
class SearchKeywordAdmin(admin.ModelAdmin):
    list_display = ("keyword", "campaign", "used", "used_at")
    list_filter = ("used", "campaign")
    raw_id_fields = ("campaign",)


@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ("action_type", "linkedin_profile", "campaign", "created_at")
    list_filter = ("action_type", "campaign")
    raw_id_fields = ("linkedin_profile", "campaign")
    date_hierarchy = "created_at"
    readonly_fields = ("linkedin_profile", "campaign", "action_type", "created_at")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("task_type", "status", "scheduled_at", "payload", "created_at")
    list_filter = ("task_type", "status")
    readonly_fields = (
        "task_type", "status", "scheduled_at", "payload",
        "created_at", "started_at", "completed_at",
    )
    date_hierarchy = "scheduled_at"


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("content_type", "object_id", "owner", "creation_date")
    list_filter = ("content_type", "owner")
    raw_id_fields = ("owner", "answer_to", "topic")
    date_hierarchy = "creation_date"
    readonly_fields = ("content_type", "object_id", "content", "owner", "creation_date")


class WatchedSourceForm(forms.ModelForm):
    def clean_identifier(self):
        identifier = self.cleaned_data.get("identifier")
        kind_value = self.cleaned_data.get("kind")
        if identifier and kind_value:
            # kind_value may be a WatchedSourceKind StrEnum or a string
            if isinstance(kind_value, str):
                # Map string to WatchedSourceKind
                kind_str_map = {
                    "own_profile": WatchedSourceKind.OWN_PROFILE,
                    "competitor_company": WatchedSourceKind.COMPETITOR_COMPANY,
                    "influencer_profile": WatchedSourceKind.INFLUENCER_PROFILE,
                }
                kind = kind_str_map.get(kind_value)
                if kind is None:
                    raise ValueError(f"Unknown kind value: {kind_value}")
            else:
                kind = kind_value
            return normalize_watched_source_identifier(identifier, kind)
        return identifier

    class Meta:
        model = WatchedSource
        fields = "__all__"


class StatusFilter(admin.SimpleListFilter):
    title = "Status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return (
            ("healthy", "Healthy"),
            ("stale", "Stale"),
            ("failing", "Failing"),
        )

    def queryset(self, request, queryset):
        if self.value() == "healthy":
            now = timezone.now()
            healthy_ids = []
            for source in queryset.filter(consecutive_failures=0, last_successful_poll_at__isnull=False):
                threshold = now - timedelta(minutes=2 * source.cadence_minutes)
                if source.last_successful_poll_at >= threshold:
                    healthy_ids.append(source.id)
            return queryset.filter(id__in=healthy_ids)
        elif self.value() == "stale":
            now = timezone.now()
            stale_ids = []
            for source in queryset.filter(consecutive_failures=0, last_poll_at__isnull=False):
                threshold = now - timedelta(minutes=2 * source.cadence_minutes)
                if source.last_poll_at < threshold:
                    stale_ids.append(source.id)
            return queryset.filter(id__in=stale_ids)
        elif self.value() == "failing":
            return queryset.filter(consecutive_failures__gte=1)
        return queryset


@admin.register(WatchedSource)
class WatchedSourceAdmin(admin.ModelAdmin):
    form = WatchedSourceForm
    list_display = (
        "campaign", "kind", "display_name", "identifier",
        "is_active", "cadence_minutes", "last_poll_at", "consecutive_failures",
        "last_successful_poll_at",
    )
    list_filter = ("kind", "is_active", "campaign", StatusFilter)
    search_fields = ("display_name", "identifier")

    actions = ["enable_selected_sources", "disable_selected_sources", "reset_failure_count", "poll_now"]

    @admin.action(description="Enable selected sources")
    def enable_selected_sources(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Disable selected sources")
    def disable_selected_sources(self, request, queryset):
        queryset.update(is_active=False)

    @admin.action(description="Reset failure count")
    def reset_failure_count(self, request, queryset):
        queryset.update(consecutive_failures=0, last_error="")

    @admin.action(description="Poll selected sources now")
    def poll_now(self, request, queryset):
        for source in queryset:
            enqueue_poll_watched_source(source.id)

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        if hasattr(response, 'context_data') and 'cl' in response.context_data:
            queryset = response.context_data['cl'].queryset
            unhealthy = queryset.filter(consecutive_failures__gte=1)
            for source in unhealthy:
                messages.warning(
                    request,
                    f"Source {source.display_name} has {source.consecutive_failures} consecutive failures. Last error: {source.last_error}"
                )
        return response


@admin.register(Signal)
class SignalAdmin(admin.ModelAdmin):
    list_display = (
        "profile_urn", "kind", "engagement_type", "score",
        "watched_source", "created_at", "post_excerpt",
    )
    list_filter = ("kind", "engagement_type", "watched_source__campaign")
    search_fields = ("profile_urn", "watched_source__display_name", "post_urn")
    date_hierarchy = "created_at"
    readonly_fields = (
        "profile_urn", "company_urn", "watched_source", "kind", "engagement_type",
        "post_urn", "post_excerpt", "post_author_urn", "post_published_at",
        "payload_json", "score", "created_at",
    )
    actions = ["export_signals_csv"]

    @admin.action(description="Export selected signals as CSV")
    def export_signals_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=signals.csv"

        writer = csv.writer(response)
        writer.writerow([
            "profile_urn", "post_urn", "engagement_type", "score",
            "kind", "source_kind", "source_name", "created_at",
        ])
        for signal in queryset.select_related("watched_source"):
            writer.writerow([
                signal.profile_urn,
                signal.post_urn,
                signal.engagement_type,
                signal.score,
                signal.kind,
                signal.watched_source.kind,
                signal.watched_source.display_name,
                signal.created_at.isoformat(),
            ])
        return response


@admin.register(SignalRadarState)
class SignalRadarStateAdmin(admin.ModelAdmin):
    list_display = ("paused_until", "paused_status", "paused_countdown")

    def paused_status(self, obj):
        from django.utils import timezone
        if obj.paused_until and obj.paused_until > timezone.now():
            return f"Currently paused: yes (until {obj.paused_until.strftime('%Y-%m-%d %H:%M UTC')})"
        return "Currently paused: no"
    paused_status.short_description = "Paused Status"

    def paused_countdown(self, obj):
        from django.utils import timezone
        if obj.paused_until and obj.paused_until > timezone.now():
            delta = obj.paused_until - timezone.now()
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes = remainder // 60
            if hours > 0:
                return f"{hours}h {minutes}m remaining"
            return f"{minutes}m remaining"
        return ""
    paused_countdown.short_description = "Countdown"

    actions = ["pause_polling_globally", "resume_polling_globally"]

    @admin.action(description="Pause Signal Radar globally for 4 hours")
    def pause_polling_globally(self, request, queryset):
        from django.utils import timezone
        from datetime import timedelta
        state = SignalRadarState.load()
        state.paused_until = timezone.now() + timedelta(hours=SIGNAL_RATE_LIMIT_PAUSE_HOURS)
        state.save()

    @admin.action(description="Resume Signal Radar globally")
    def resume_polling_globally(self, request, queryset):
        state = SignalRadarState.load()
        state.paused_until = None
        state.save()