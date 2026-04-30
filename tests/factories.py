# tests/factories.py
import factory
from django.contrib.auth.models import User
from faker import Faker

from linkedin.models import Campaign, Signal, WatchedSource

fake = Faker()


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.LazyFunction(fake.user_name)
    is_staff = True
    is_active = True


class CampaignFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Campaign

    name = factory.Sequence(lambda n: f"Campaign {n}")


class LeadFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Lead"

    public_identifier = factory.Sequence(lambda n: f"lead-{n}")
    linkedin_url = factory.LazyAttribute(
        lambda o: f"https://www.linkedin.com/in/{o.public_identifier}/"
    )


class DealFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "crm.Deal"

    lead = factory.SubFactory(LeadFactory)


class WatchedSourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WatchedSource

    campaign = factory.SubFactory(CampaignFactory)
    kind = WatchedSource.Kind.OWN_PROFILE
    identifier = factory.Sequence(lambda n: f"urn:li:company:{n}")
    display_name = factory.LazyFunction(lambda: "Test Company")


class SignalFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Signal

    campaign = factory.SubFactory(CampaignFactory)
    watched_source = factory.SubFactory(WatchedSourceFactory)
    kind = Signal.Kind.OWN_POST_ENGAGEMENT
    engagement_type = Signal.EngagementType.REACTION
    post_urn = factory.Sequence(lambda n: f"urn:li:post:{n}")
    post_excerpt = ""
    post_author_urn = ""
    score = 0
