import asyncio
from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand
from grading_api.models import ScrapeLog
from grading_api.views import PokemonCardViewSet

class Command(BaseCommand):
    help = "Scrapes all sets."

    def handle(self, *args, **kwargs):
        # Create a ScrapeLog instance or load an existing one
        scrape_log = ScrapeLog.objects.create(
            user="cron_job",
            source=ScrapeLog.Source.ALL,
            status=ScrapeLog.Status.IN_PROGRESS
        )

        viewset = PokemonCardViewSet()
        
        # Use async_to_sync to run the async scraping population.
        try:
            async_to_sync(viewset._scrape_all_sets_async)(scrape_log.pk)
            self.stdout.write(self.style.SUCCESS("Successfully ran scrape_all_sets."))
        except Exception as e:
            self.stderr.write(f"Error running scrape_all_sets: {e}")