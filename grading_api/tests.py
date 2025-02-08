import pytest
from django.urls import reverse
from rest_framework import status
from asgiref.sync import async_to_sync
from unittest.mock import patch
import aiohttp
from .models import PokemonCard
from .scraper import CardDetails, CardPriceData
from .views import PokemonCardViewSet

# Model Tests
@pytest.mark.django_db
class TestPokemonCardModel:
    def test_pokemon_card_creation(self, pokemon_card_factory):
        card = pokemon_card_factory()
        assert PokemonCard.objects.count() == 1
        assert card.card_name.startswith("Charizard")

    def test_str_representation(self, pokemon_card_factory):
        card = pokemon_card_factory(card_name="Pikachu", set_name="Base Set")
        assert str(card) == "Pikachu - Base Set"

# View Tests (with Mock Scraper)
@pytest.mark.django_db
class TestPokemonCardViewSet:
    @pytest.fixture(autouse=True)
    def setup(self, mock_scraper):
        self.viewset = PokemonCardViewSet(scraper=mock_scraper)

    def test_scrape_and_save_success(self, api_client, mock_scraper):
        url = reverse("pokemoncard-scrape-and-save")
        data = {"searchQuery": "Mew EX", "set_name": "Pokemon Card 151", "language": "Japanese"}
        response = api_client.get(url, data)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["message"] == "Successfully processed 1 cards"
        assert PokemonCard.objects.count() == 1

    def test_scrape_and_save_no_query(self, api_client):
        url = reverse("pokemoncard-scrape-and-save")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "Please provide a search query"

    def test_scrape_and_save_no_data(self, api_client, mock_scraper):
        mock_scraper.scrape_card_data = async_to_sync(lambda x: [])  # Mock no data
        url = reverse("pokemoncard-scrape-and-save")
        data = {"searchQuery": "Nonexistent Card"}
        response = api_client.get(url, data)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "No data found for Nonexistent Card"

    def test_refresh_success(self, api_client, pokemon_card_factory, mock_scraper):
        card = pokemon_card_factory()
        url = reverse("pokemoncard-refresh", kwargs={"pk": card.pk})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["card_name"] == card.card_name  # Check updated data

    def test_refresh_not_found(self, api_client):
        url = reverse("pokemoncard-refresh", kwargs={"pk": 9999})  # Nonexistent ID
        response = api_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data["error"] == "Card not found"

# Scraper Tests (Integration Tests - Use Sparingly)
@pytest.mark.django_db
@pytest.mark.integration
class TestMyCustomScraper:
    @pytest.fixture(autouse=True)
    def setup(self, real_scraper):
        self.scraper = real_scraper

    @pytest.mark.asyncio
    async def test_scrape_card_data_success(self):
        card_details = [CardDetails(name="Charizard EX", set_name="Obsidian Flames", language="English")]
        results = await self.scraper.scrape_card_data(card_details)

        assert len(results) > 0
        assert results[0].card_name == "Charizard EX"
        assert results[0].set_name == "Obsidian Flames"
        assert isinstance(results[0].tcgplayer_price, float)

    @pytest.mark.asyncio
    async def test_scrape_card_data_no_results(self):
        card_details = [CardDetails(name="Nonexistent Card", set_name="Fake Set", language="English")]
        results = await self.scraper.scrape_card_data(card_details)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_get_ebay_psa10_price_async_success(self):
        card_details = CardDetails(name="Charizard EX", set_name="Obsidian Flames", language="English")
        async with aiohttp.ClientSession() as session:
            price = await self.scraper.get_ebay_psa10_price_async(session, card_details)

        assert isinstance(price, float) or price is None

    @pytest.mark.asyncio
    async def test_get_ebay_psa10_price_async_no_results(self):
        card_details = CardDetails(name="Nonexistent Card", set_name="Fake Set", language="English")
        async with aiohttp.ClientSession() as session:
            price = await self.scraper.get_ebay_psa10_price_async(session, card_details)

        assert price is None
