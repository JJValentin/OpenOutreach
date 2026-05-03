import django
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")
django.setup()

from linkedin.models import LinkedInProfile, Campaign, WatchedSource

profile = LinkedInProfile.objects.filter(active=True).first()
campaign = Campaign.objects.filter(users=profile.user).first()

if campaign:
    existing_count = WatchedSource.objects.filter(campaign=campaign).count()
    print(f"Campaign: {campaign.name} (ID: {campaign.pk}), existing sources: {existing_count}")
    
    if existing_count == 0:
        WatchedSource.objects.create(
            campaign=campaign,
            kind= ' OWN_PROFILE ' ,
            identifier= ' joshua-valentin ' ,
            display_name= ' Joshua Valentin ' ,
            cadence_minutes=240,
            is_active=True,
        )
        WatchedSource.objects.create(
            campaign=campaign,
            kind= ' COMPETITOR_COMPANY ' ,
            identifier= ' linkedin ' ,
            display_name= ' LinkedIn ' ,
            cadence_minutes=240,
            is_active=True,
        )
        print("Created 2 watched sources")
    else:
        print(f"Already have {existing_count} sources")
