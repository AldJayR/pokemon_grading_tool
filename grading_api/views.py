from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.utils import timezone
from django.db import transaction
from django_filters import rest_framework as filters
import logging
import asyncio
from functools import wraps
from asgiref.sync import sync_to_async

from .models import PokemonCard
from .serializers import PokemonCardSerializer
from . import scraper

logger = logging.getLogger(__name__)

def async_action(method):
    """Decorator to handle async actions in DRF views"""
    @wraps(method)
    def wrapper(self, request, *args, **kwargs):
        return asyncio.run(method(self, request, *args, **kwargs))
    return wrapper

class PokemonCardFilter(filters.FilterSet):
    card_name = filters.CharFilter(field_name='card_name', lookup_expr='icontains')
    set_name = filters.CharFilter(field_name='set_name', lookup_expr='icontains')
    language = filters.ChoiceFilter(
        choices=PokemonCard.LANGUAGE_CHOICES,
        field_name='language'
    )
    
    price_range = filters.RangeFilter(field_name='tcgplayer_price')
    profit_range = filters.RangeFilter(field_name='profit_potential')
    
    class Meta:
        model = PokemonCard
        fields = ['card_name', 'set_name', 'language', 'rarity']

class PokemonCardViewSet(viewsets.ModelViewSet):
    queryset = PokemonCard.objects.all()
    serializer_class = PokemonCardSerializer
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = PokemonCardFilter

    @sync_to_async
    def save_card_to_db(self, card_dict):
        """Async wrapper for database operations"""
        return PokemonCard.objects.update_or_create(
            card_name=card_dict['card_name'],
            set_name=card_dict['set_name'],
            language=card_dict['language'],
            rarity=card_dict['rarity'],
            defaults=card_dict
        )

    @action(detail=False, methods=['get'])
    @async_action
    async def scrape_and_save(self, request):
        """
        Scrape card data and save to database
        """
        search_query = request.query_params.get('searchQuery', '').strip()
        language = request.query_params.get('language', 'English')

        if not search_query:
            return Response(
                {'error': 'Please provide a card or set name in query params'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Create CardDetails object for the scraper
            card_details = scraper.CardDetails(
                name=search_query,
                set_name=search_query,
                language=language
            )

            # Run the scraper
            logger.info(f"Fetching data for {search_query} ({language})...")
            all_profit_data = await scraper.main([card_details])

            if not all_profit_data:
                return Response(
                    {'error': f'No data found for {search_query}'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Save results to database
            saved_cards = []
            for card_data in all_profit_data:
                card_dict = {
                    'card_name': card_data.card_name,
                    'set_name': card_data.set_name,
                    'language': card_data.language,
                    'rarity': card_data.rarity,
                    'tcgplayer_price': card_data.tcgplayer_price,
                    'psa_10_price': card_data.psa_10_price,
                    'price_delta': card_data.price_delta,
                    'profit_potential': card_data.profit_potential,
                }

                card_record, created = await self.save_card_to_db(card_dict)
                saved_cards.append(card_record)

            if not saved_cards:
                return Response(
                    {'error': 'Failed to save card data'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            serializer = self.serializer_class(saved_cards, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.error(f"Error processing request: {str(e)}", exc_info=True)
            return Response(
                {'error': f'An unexpected error occurred: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )