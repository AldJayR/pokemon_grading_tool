from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

class PokemonCard(models.Model):
    LANGUAGE_CHOICES = [
        ('English', 'English'),
        ('Japanese', 'Japanese'),
    ]
    
    card_name = models.CharField(max_length=255, db_index=True)
    set_name = models.CharField(max_length=255, db_index=True)
    language = models.CharField(
        max_length=20,
        choices=LANGUAGE_CHOICES,
        default='English'
    )
    rarity = models.CharField(max_length=100, default="Unknown")
    
    # Price information
    tcgplayer_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)]
    )
    psa_10_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)]
    )
    price_delta = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    profit_potential = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(-100), MaxValueValidator(1000)]
    )
    
    # Metadata
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['card_name', 'set_name', 'language']),
            models.Index(fields=['-profit_potential']),
            models.Index(fields=['last_updated']),
        ]
        unique_together = ['card_name', 'set_name', 'language', 'rarity']
        ordering = ['-profit_potential', 'card_name']

    def __str__(self):
        return f"{self.card_name} ({self.set_name}) - {self.rarity}"

    def clean(self):
        if self.tcgplayer_price and self.psa_10_price:
            self.price_delta = self.psa_10_price - self.tcgplayer_price
            if self.tcgplayer_price > 0:
                self.profit_potential = (self.price_delta / self.tcgplayer_price) * 100