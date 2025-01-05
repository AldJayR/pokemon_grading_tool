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
        try:
            card, created = PokemonCard.objects.update_or_create(
                card_name=card_dict['card_name'],
                set_name=card_dict['set_name'],
                language=card_dict['language'],
                rarity=card_dict['rarity'],
                defaults=card_dict
            )
            return card
        except Exception as e:
            logger.error(f"Error saving card to database: {str(e)}")
            return None

    @action(detail=False, methods=['get'])
    def scrape_and_save(self, request):
        """
        Scrape card data and save to database. Supports three search modes:
        1. Card name only: /api/cards/scrape_and_save/?searchQuery=Pikachu
        2. Set name only: /api/cards/scrape_and_save/?searchQuery=SV08: Surging Sparks
        3. Card name with set: /api/cards/scrape_and_save/?searchQuery=Pikachu&set_name=SV08: Surging Sparks
        """
        search_query = request.query_params.get('searchQuery', '').strip()
        set_name = request.query_params.get('set_name', '').strip()
        language = request.query_params.get('language', 'English')

        if not search_query:
            return Response(
                {'error': 'Please provide a search query'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            def run_scraper():
                async def async_scrape():
            # Determine search type and create appropriate CardDetails
                    is_set_search = "SV" in search_query or ":" in search_query

                    # If it's a set search, use empty name and search_query as set_name
                    if is_set_search:
                        card_details = scraper.CardDetails(
                            name="",
                            set_name=search_query,
                            language=language
                        )
                    else:
                        # If set_name parameter is provided, use both
                        if set_name:
                            card_details = scraper.CardDetails(
                                name=search_query,
                                set_name=set_name,
                                language=language
                            )
                        else:
                            # Card name search only
                            card_details = scraper.CardDetails(
                                name=search_query,
                                set_name="",
                                language=language
                            )

                    logger.info(f"Starting search for: {search_query} "
                            f"(Set: {card_details.set_name}, Language: {language})")
                    return await scraper.main([card_details])

                return asyncio.run(async_scrape())

            # Run the scraper
            all_profit_data = run_scraper()

            if not all_profit_data:
                return Response(
                    {'error': f'No data found for {search_query}'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Save results to database
            saved_cards = []
            with transaction.atomic():
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
                    
                    card_record, created = PokemonCard.objects.update_or_create(
                        card_name=card_dict['card_name'],
                        set_name=card_dict['set_name'],
                        language=card_dict['language'],
                        rarity=card_dict['rarity'],
                        defaults=card_dict
                    )
                    saved_cards.append(card_record)

            if not saved_cards:
                return Response(
                    {'error': 'Failed to save card data'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # Return saved cards data
            serializer = self.serializer_class(saved_cards, many=True)
            return Response(serializer.data)
        
        except Exception as e:
            logger.error(f"Error processing request: {str(e)}", exc_info=True)
            return Response(
                {'error': f'An unexpected error occurred: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'])
    async def refresh(self, request, pk=None):
        """Refresh data for a specific card"""
        try:
            card = await sync_to_async(PokemonCard.objects.get)(pk=pk)

            card_details = scraper.CardDetails(
                name=card.card_name,
                set_name=card.set_name,
                language=card.language
            )

            # Fetch updated data
            updated_data = await scraper.main([card_details])

            if not updated_data:
                return Response(
                    {'error': 'No updated data found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Update card with new data
            card_dict = self._create_card_dict(updated_data[0])
            updated_card = await self.save_card_to_db(card_dict)

            if not updated_card:
                return Response(
                    {'error': 'Failed to update card'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            serializer = self.serializer_class(updated_card)
            return Response(serializer.data)

        except PokemonCard.DoesNotExist:
            return Response(
                {'error': 'Card not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error refreshing card data: {str(e)}", exc_info=True)
            return Response(
                {'error': f'An unexpected error occurred: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )