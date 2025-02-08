from decimal import Decimal
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

class PokemonCard(models.Model):
    class Language(models.TextChoices):
        ENGLISH = 'English', 'English'
        JAPANESE = 'Japanese', 'Japanese'

    # Core card information
    card_name = models.CharField(max_length=255, db_index=True)
    set_name = models.CharField(max_length=255, db_index=True)
    product_id = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        null=True,
        blank=True,
    )
    card_number = models.CharField(max_length=50, blank=True)

    # Card details
    language = models.CharField(
        max_length=20,
        choices=Language.choices,
        default=Language.ENGLISH,
    )
    rarity = models.CharField(max_length=100, default="Unknown")

    # Price tracking fields

    tcgplayer_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    tcgplayer_market_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    tcgplayer_last_pulled = models.DateTimeField(null=True, blank=True)

    psa_10_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    ebay_last_pulled = models.DateTimeField(null=True, blank=True)

    # Metadata
    is_active = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['card_name', 'set_name', 'language']),
            models.Index(fields=['last_updated']),
            models.Index(fields=['product_id']),
            models.Index(fields=['is_active', 'last_updated']),
        ]
        unique_together = ['card_name', 'set_name', 'language', 'rarity']
        ordering = ['card_name']

    def __str__(self):
        return f"{self.card_name} ({self.set_name}) - {self.rarity} - {self.language}"

    @property
    def price_delta(self) -> str:
        """Calculate the difference between PSA 10 and TCGPlayer prices as formatted string."""
        if self.psa_10_price is None or self.tcgplayer_price is None:
            return "0.00"
        delta = float(self.psa_10_price) - float(self.tcgplayer_price)
        return f"{delta:.2f}"

    @property
    def profit_potential(self) -> str:
        """Calculate the potential profit percentage as formatted string."""
        if self.tcgplayer_price is None or float(self.tcgplayer_price) <= 0:
            return "0.00"
        if (delta := float(self.psa_10_price or 0) - float(self.tcgplayer_price)) > 0:
            potential = (delta / float(self.tcgplayer_price)) * 100
            return f"{potential:.2f}"
        return "0.00"

    def update_tcgplayer_data(self, price, market_price, product_id):
        """Update TCGPlayer data with validation."""
        if price < 0 or (market_price is not None and market_price < 0):
            raise ValueError("Prices cannot be negative")
        if not product_id:
            raise ValueError("Product ID cannot be empty")
            
        self.tcgplayer_price = price
        self.tcgplayer_market_price = market_price
        self.product_id = product_id
        self.tcgplayer_last_pulled = timezone.now()
        self.save()

    def update_ebay_data(self, psa_10_price):
        """Update eBay PSA 10 price data with validation."""
        if psa_10_price < 0:
            raise ValueError("PSA 10 price cannot be negative")
            
        self.psa_10_price = psa_10_price
        self.ebay_last_pulled = timezone.now()
        self.save()

class ScrapeLog(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        PARTIAL = 'partial', 'Partially Completed'  # New status

    class Source(models.TextChoices):  # New enum
        TCGPLAYER = 'tcgplayer', 'TCGPlayer'
        EBAY = 'ebay', 'eBay'
        ALL = 'all', 'All Sources'

    # Basic information
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    user = models.CharField(max_length=255)
    
    # Scraping source and status
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.ALL,
    )
    status = models.CharField(
        max_length=50,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )

    # Statistics
    total_cards_attempted = models.PositiveIntegerField(default=0)
    total_cards_updated = models.PositiveIntegerField(default=0)
    total_cards_failed = models.PositiveIntegerField(default=0)  # New field
    
    # Error tracking
    error_message = models.TextField(null=True, blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)  # New field
    
    # Performance metrics
    execution_time = models.DurationField(null=True, blank=True)  # New field

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['status', 'started_at']),  # New index
            models.Index(fields=['user', 'started_at']),    # New index
        ]

    def __str__(self):
        duration = self.execution_time or ''
        return f"Scrape by {self.user} at {self.started_at} - Status: {self.status} {duration}"

    def complete(self, total_cards_attempted, total_cards_updated, total_cards_failed=0):
        """Mark the scrape as complete with statistics."""
        self.completed_at = timezone.now()
        self.status = self.Status.COMPLETED
        self.total_cards_attempted = total_cards_attempted
        self.total_cards_updated = total_cards_updated
        self.total_cards_failed = total_cards_failed
        self.execution_time = self.completed_at - self.started_at
        
        # Set partial status if not all cards were updated
        if total_cards_failed > 0:
            self.status = self.Status.PARTIAL
            
        self.save()

    def fail(self, error_message):
        """Mark the scrape as failed with error details."""
        self.completed_at = timezone.now()
        self.status = self.Status.FAILED
        self.error_message = error_message
        self.execution_time = self.completed_at - self.started_at
        self.save()

    @transaction.atomic
    def update_progress(self, success_count=0, failure_count=0):
        """Atomically update scraping progress metrics."""
        ScrapeLog.objects.filter(pk=self.pk).update(
            total_cards_updated=models.F('total_cards_updated') + success_count,
            total_cards_failed=models.F('total_cards_failed') + failure_count,
            total_cards_attempted=models.F('total_cards_attempted') + (int(success_count) + int(failure_count))
        )
        self.refresh_from_db()

    @transaction.atomic
    def log_error(self, error_message, increment_retry=True):
        """Record an error occurrence with atomic update."""
        update_kwargs = {
            'error_message': error_message,
            'total_cards_failed': models.F('total_cards_failed') + 1
        }
        
        if increment_retry:
            update_kwargs['retry_count'] = models.F('retry_count') + 1
            
        ScrapeLog.objects.filter(pk=self.pk).update(**update_kwargs)
        self.refresh_from_db()

    @property
    def success_rate(self):
        """Calculate the success rate of the scrape."""
        if self.total_cards_attempted == 0:
            return 0
        return (self.total_cards_updated / self.total_cards_attempted) * 100
