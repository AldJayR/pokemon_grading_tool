from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from django.utils import timezone
from django.db import transaction
from django.core.cache import cache
from django.db.models import Q
from django_filters import rest_framework as filters
import logging
from functools import wraps
from asgiref.sync import sync_to_async, async_to_sync
from typing import List, Optional, Dict, Any
import asyncio
from datetime import timedelta
from rest_framework.pagination import PageNumberPagination
import gc
import psutil

from .models import PokemonCard, ScrapeLog
from .serializers import PokemonCardSerializer
from . import scraper

logger = logging.getLogger(__name__)

class CardSetData:  # Keep this for the scrape_all_sets functionality
    ENGLISH_SETS = [
        "SV08: Surging Sparks",
        "SV07: Stellar Crown",
        "SV06: Twilight Masquerade",
        "SV05: Temporal Forces",
        "SV04: Paradox Rift",
        "SV03: Obsidian Flames",
        "SV: Shrouded Fable",
        "SV: Scarlet & Violet 151",
        "SV: Paldean Fates",
    ]

    JAPANESE_SETS = [
        "SV7A: Paradise Dragona",
        "SV7: Stellar Miracle",
        "SV6A: Night Wanderer",
        "SV6: Transformation Mask",
        "SV5M: Cyber Judge",
        "SV5K: Wild Force",
        "SV5A: Crimson Haze",
        "SV-P Promotional Cards",
        "SV: Ancient Koraidon ex Starter Deck & Build Set",
        "SV8a: Terastal Fest ex",
        "SV8: Super Electric Breaker"
    ]

    ENGLISH_RARITIES = [
        "Special Illustration Rare",
        "Illustration Rare",
        "Hyper Rare"
    ]

    JAPANESE_RARITIES = [
        "Art Rare",
        "Super Rare",
        "Special Art Rare",
        "Ultra Rare"
    ]

    ALL_SETS = ENGLISH_SETS + JAPANESE_SETS  # Combine for validation

class PokemonCardFilter(filters.FilterSet):
    card_name = filters.CharFilter(field_name='card_name', lookup_expr='icontains')
    set_name = filters.CharFilter(field_name='set_name', lookup_expr='icontains')
    language = filters.ChoiceFilter(
        choices=PokemonCard.Language.choices,
        field_name='language'
    )
    rarity = filters.CharFilter(field_name='rarity', lookup_expr='icontains')
    price_range = filters.RangeFilter(field_name='tcgplayer_price')
    profit_range = filters.RangeFilter(field_name='profit_potential')

    class Meta:
        model = PokemonCard
        fields = ['card_name', 'set_name', 'language', 'rarity']

class StandardResultsSetPagination(PageNumberPagination):
    """Standard pagination class for the viewset."""
    page_size = 100  # Default page size
    page_size_query_param = 'page_size'
    max_page_size = 1000

class PokemonCardViewSet(viewsets.ModelViewSet):
    CACHE_TIMEOUT = 3600
    MAX_CONCURRENT_REQUESTS = 2  # Reduced from 3
    BATCH_SIZE = 2
    INTER_SET_DELAY = 5  # seconds between sets
    INTER_BATCH_DELAY = 10  # seconds between batches
    MAX_RETRIES_PER_SET = 3
    pagination_class = StandardResultsSetPagination  # Add pagination

    queryset = PokemonCard.objects.all()
    serializer_class = PokemonCardSerializer
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = PokemonCardFilter

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)

    async def _process_card_data(self, card_data: scraper.CardPriceData) -> dict:
        """Process card data, handling None values explicitly."""
        try:
            return {
                'card_name': card_data.card_name,
                'set_name': card_data.set_name,
                'language': card_data.language,
                'rarity': card_data.rarity,
                'tcgplayer_price': card_data.tcgplayer_price or 0.0,
                'tcgplayer_last_pulled': timezone.now(),
                'product_id': card_data.product_id,
                'psa_10_price': card_data.psa_10_price or 0.0,
                'ebay_last_pulled': timezone.now(),
                'price_delta': card_data.price_delta or 0.0,
                'profit_potential': card_data.profit_potential or 0.0,
                'last_updated': timezone.now()
            }
        except (ValueError, AttributeError) as e:
            logger.error(f"Data processing error: {str(e)}")
            raise ValueError(f"Invalid card data: {str(e)}")

    @sync_to_async
    def _save_card_to_db(self, card_dict: dict) -> Optional[PokemonCard]:
        """Save or update card data."""
        try:
            with transaction.atomic():
                card, created = PokemonCard.objects.update_or_create(
                    card_name=card_dict['card_name'],
                    set_name=card_dict['set_name'],
                    language=card_dict['language'],
                    rarity=card_dict['rarity'],
                    defaults={
                        'tcgplayer_price': card_dict['tcgplayer_price'],
                        'tcgplayer_last_pulled': card_dict['tcgplayer_last_pulled'],
                        'product_id': card_dict.get('product_id'),
                        'psa_10_price': card_dict['psa_10_price'],
                        'ebay_last_pulled': card_dict['ebay_last_pulled'],
                        'last_updated': card_dict['last_updated'],
                    }
                )
                return card
        except Exception as e:
            logger.error(f"Database error: {str(e)}", exc_info=True)
            return None

    def _create_card_details(self, search_query: str, set_name: str, language: str) -> scraper.CardDetails:
        """Create CardDetails, validating set_name if provided."""
        is_set_search = "SV" in search_query or ":" in search_query

        # Validate set_name against predefined sets
        if set_name and set_name not in CardSetData.ALL_SETS:
            raise ValueError(f"Invalid set_name: {set_name}")

        if is_set_search:
            return scraper.CardDetails(name="", set_name=search_query, language=language)
        elif set_name:
            return scraper.CardDetails(name=search_query, set_name=set_name, language=language)
        return scraper.CardDetails(name=search_query, set_name="", language=language)

    def _check_memory_usage(self):
        """Monitor memory usage and force collection if needed."""
        process = psutil.Process()
        memory_info = process.memory_info()
        
        # If using more than 80% of available memory, force collection
        if memory_info.rss > (psutil.virtual_memory().available * 0.8):
            gc.collect()

    # --- Actions ---

    @action(detail=False, methods=['get'])
    def scrape_and_save(self, request):
        """Scrape card data and save to database."""
        return async_to_sync(self._scrape_and_save_async)(request)

    async def _scrape_and_save_async(self, request):
        """Asynchronous implementation of scrape_and_save."""
        search_query = request.query_params.get('searchQuery', '').strip()
        set_name = request.query_params.get('set_name', '').strip()
        language = request.query_params.get('language', 'English')
        # Use request.user if authenticated, otherwise default to 'anonymous'
        user = request.user if request.user.is_authenticated else 'anonymous'

        if not search_query:
            return Response(
                {'error': 'Search query is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # More robust cache key
        cache_key = f"scrape:{search_query}:{set_name}:{language}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return Response(cached_result)

        scrape_log = await sync_to_async(ScrapeLog.objects.create)(user=str(user)) # Use str(user)

        try:
            async with self._request_semaphore:
                card_details = self._create_card_details(search_query, set_name, language)
                logger.info(f"Starting search: {search_query} (Set: {card_details.set_name}, Language: {language})")

                try:
                    profit_data = await scraper.main([card_details])
                except Exception as e:  # Catch scraper-specific exceptions
                    logger.error(f"Scraper error: {str(e)}", exc_info=True)
                    await sync_to_async(scrape_log.fail)(f"Scraper error: {str(e)}")
                    return Response(
                        {'error': f'Scraper error: {str(e)}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

                if not profit_data:
                    await sync_to_async(scrape_log.fail)("No data found")
                    return Response(
                        {'error': f'No data found for {search_query}'},
                        status=status.HTTP_404_NOT_FOUND
                    )

                saved_cards = []
                for card_data in profit_data:
                    try:
                        card_dict = await self._process_card_data(card_data)
                        if card := await self._save_card_to_db(card_dict):
                            saved_cards.append(card)
                    except ValueError as e:
                        logger.warning(f"Skipping invalid card: {str(e)}")
                        continue

                if not saved_cards:
                    await sync_to_async(scrape_log.fail)("Failed to save any card data")
                    return Response(
                        {'error': 'Failed to save any card data'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

                await sync_to_async(scrape_log.complete)(len(profit_data), len(saved_cards))
                serializer = self.serializer_class(saved_cards, many=True)
                response_data = {
                    'message': f'Successfully processed {len(saved_cards)} cards',
                    'cards': serializer.data,
                    'log_id': scrape_log.id
                }
                cache.set(cache_key, response_data, self.CACHE_TIMEOUT)
                return Response(response_data)

        except ValueError as e:  # Catch validation errors from _create_card_details
            logger.error(f"Input validation error: {str(e)}", exc_info=True)
            await sync_to_async(scrape_log.fail)(f"Input validation error: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST  # 400 for client errors
            )
        except Exception as e:
            logger.error(f"Request error: {str(e)}", exc_info=True)
            await sync_to_async(scrape_log.fail)(str(e))
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'])
    def refresh(self, request, pk=None):
        """Refresh data for a specific card."""
        return async_to_sync(self._refresh_async)(request, pk)

    async def _refresh_async(self, request, pk):
        """Asynchronous implementation of refresh."""
        user = request.user if request.user.is_authenticated else 'anonymous'
        try:
            card = await sync_to_async(PokemonCard.objects.select_for_update().get)(pk=pk)

            if not card.product_id:
                return Response(
                    {'error': 'Card missing product ID'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            scrape_log = await sync_to_async(ScrapeLog.objects.create)(user=str(user))

            async with self._request_semaphore:
                card_details = scraper.CardDetails(
                    name=card.card_name,
                    set_name=card.set_name,
                    language=card.language,
                    product_id=card.product_id
                )

                try:
                    updated_data = await scraper.main([card_details])
                except Exception as e:  # Catch scraper-specific exceptions
                    logger.error(f"Scraper error during refresh: {str(e)}", exc_info=True)
                    await sync_to_async(scrape_log.fail)(f"Scraper error: {str(e)}")
                    return Response(
                        {'error': f'Scraper error: {str(e)}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

                if not updated_data:
                    await sync_to_async(scrape_log.fail)("No updated data found")
                    return Response(
                        {'error': 'No updated data found'},
                        status=status.HTTP_404_NOT_FOUND
                    )

                try:
                    card_dict = await self._process_card_data(updated_data[0])
                    updated_card = await self._save_card_to_db(card_dict)

                    # Invalidate cache (more targeted invalidation)
                    cache_key_prefix = f"scrape:{card.card_name}:{card.set_name}:{card.language}"
                    cache.delete_pattern(f"{cache_key_prefix}*")


                    await sync_to_async(scrape_log.complete)(1, 1)
                    serializer = self.serializer_class(updated_card)
                    return Response(serializer.data)

                except ValueError as e:
                    await sync_to_async(scrape_log.fail)(str(e))
                    return Response(
                        {'error': f'Invalid card data: {str(e)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

        except PokemonCard.DoesNotExist:
            return Response(
                {'error': 'Card not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Refresh error: {str(e)}", exc_info=True)
            await sync_to_async(scrape_log.fail)(str(e))
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'])
    def scrape_all_sets(self, request):
        """Scrape all sets concurrently and save to database."""
        user = request.user if request.user.is_authenticated else 'anonymous'
        scrape_log = ScrapeLog.objects.create(user=str(user))  # Create log entry

        async_to_sync(self._scrape_all_sets_async)(scrape_log.id)  # Start async task

        return Response({
            'message': 'Scrape started',
            'log_id': scrape_log.id
        })

    async def _scrape_all_sets_async(self, log_id):
        """Asynchronous implementation of scrape_all_sets with batching."""
        try:
            scrape_log = await sync_to_async(ScrapeLog.objects.get)(id=log_id)
            total_attempted = 0
            total_updated = 0

            # Process sets in smaller batches
            batch_size = 2  # Process 2 sets at a time
            
            async def process_set_batch(sets_batch, language, rarities):
                nonlocal total_attempted, total_updated
                
                for set_name in sets_batch:
                    try:
                        async with self._request_semaphore:
                            card_details = scraper.CardDetails(
                                name="", 
                                set_name=set_name, 
                                language=language
                            )
                            results = await scraper.main([card_details])
                            
                            # Update progress
                            await sync_to_async(scrape_log.update_progress)(
                                f"Processing {language} set: {set_name}"
                            )
                            
                            batch_attempted = len(results)
                            batch_updated = 0
                            
                            for card_data in results:
                                if card_data.rarity in rarities:
                                    try:
                                        card_dict = await self._process_card_data(card_data)
                                        if await self._save_card_to_db(card_dict):
                                            batch_updated += 1
                                    except ValueError:
                                        continue
                            
                            total_attempted += batch_attempted
                            total_updated += batch_updated
                            
                            # Force garbage collection after each set
                            import gc
                            gc.collect()
                            
                            # Add delay between sets
                            await asyncio.sleep(5)
                            
                    except Exception as e:
                        logger.error(f"Error processing {set_name}: {e}")
                        await sync_to_async(scrape_log.log_error)(
                            f"Error in set {set_name}: {str(e)}"
                        )
                        continue

            # Process English sets in batches
            for i in range(0, len(CardSetData.ENGLISH_SETS), batch_size):
                batch = CardSetData.ENGLISH_SETS[i:i + batch_size]
                await process_set_batch(batch, "English", CardSetData.ENGLISH_RARITIES)

            # Process Japanese sets in batches
            for i in range(0, len(CardSetData.JAPANESE_SETS), batch_size):
                batch = CardSetData.JAPANESE_SETS[i:i + batch_size]
                await process_set_batch(batch, "Japanese", CardSetData.JAPANESE_RARITIES)

            await sync_to_async(scrape_log.complete)(total_attempted, total_updated)

        except Exception as e:
            logger.error(f"Fatal error in bulk scraping: {str(e)}", exc_info=True)
            await sync_to_async(scrape_log.fail)(str(e))

    # --- Fetch Actions ---
    # Keep these, but use the filterset and pagination

    @action(detail=False, methods=['get'], url_path='fetch_card')
    def fetch_card(self, request):
        """Fetch specific card by name."""
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='fetch_card_set')
    def fetch_card_set(self, request):
        """Fetch specific card by name and set."""
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='fetch_card_rarity')
    def fetch_card_rarity(self, request):
        """Fetch specific card by name and rarity."""
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='fetch_set')
    def fetch_set(self, request):
        """Fetch cards by set."""
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='fetch_set_rarity')
    def fetch_set_rarity(self, request):
        """Fetch cards by set and rarity."""
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def list(self, request, *args, **kwargs):
        """Get cards from database with pagination and freshness check."""
        queryset = self.filter_queryset(self.get_queryset())

        # Check freshness (optional)
        fresh_data_cutoff = timezone.now() - timedelta(hours=24)
        is_fresh = queryset.filter(last_updated__gte=fresh_data_cutoff).exists()

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data['is_fresh'] = is_fresh  # Add to paginated response
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'message': 'Retrieved from database',
            'cards': serializer.data,
            'is_fresh': is_fresh
        })
