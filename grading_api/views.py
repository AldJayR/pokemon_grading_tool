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
from typing import List, Optional, Dict, Any, TypeVar
import asyncio
from datetime import timedelta
from rest_framework.pagination import PageNumberPagination
import gc
import psutil
from dataclasses import dataclass
from django.conf import settings
import re
import aiohttp
import ssl
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
import threading

from .models import PokemonCard, ScrapeLog
from .serializers import PokemonCardSerializer
from . import scraper

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)
aiohttp_logger = logging.getLogger('aiohttp')
aiohttp_logger.setLevel(logging.DEBUG)

T = TypeVar('T')  # Type variable for generic functions

@dataclass
class CardSetData:
    """Structured card set data with validation methods."""
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

    @classmethod
    def get_all_sets(cls) -> List[str]:
        """Get combined list of all sets."""
        return cls.ENGLISH_SETS + cls.JAPANESE_SETS

    @classmethod
    def validate_set(cls, set_name: str) -> bool:
        """Validate if a set name is valid."""
        return set_name in cls.get_all_sets()

class PokemonCardFilter(filters.FilterSet):
    """FilterSet for PokemonCard with enhanced filtering capabilities."""
    card_name = filters.CharFilter(field_name='card_name', lookup_expr='icontains')
    set_name = filters.CharFilter(field_name='set_name', lookup_expr='icontains')
    language = filters.ChoiceFilter(
        choices=PokemonCard.Language.choices,
        field_name='language'
    )
    rarity = filters.CharFilter(field_name='rarity', lookup_expr='icontains')
    price_range = filters.RangeFilter(field_name='tcgplayer_price')
    profit_range = filters.RangeFilter(field_name='profit_potential')
    last_updated = filters.DateTimeFromToRangeFilter()

    class Meta:
        model = PokemonCard
        fields = ['card_name', 'set_name', 'language', 'rarity']

class StandardResultsSetPagination(PageNumberPagination):
    """Configurable pagination for the viewset."""
    page_size = getattr(settings, 'POKEMON_CARD_PAGE_SIZE', 100)
    page_size_query_param = 'page_size'
    max_page_size = getattr(settings, 'POKEMON_CARD_MAX_PAGE_SIZE', 1000)

class PokemonCardViewSet(viewsets.ModelViewSet):
    """ViewSet for managing Pokemon card data with async scraping capabilities."""
    
    # Configuration constants
    CACHE_TIMEOUT = getattr(settings, 'POKEMON_CARD_CACHE_TIMEOUT', 3600)
    MAX_CONCURRENT_REQUESTS = getattr(settings, 'POKEMON_CARD_MAX_CONCURRENT_REQUESTS', 2)
    BATCH_SIZE = getattr(settings, 'POKEMON_CARD_BATCH_SIZE', 2)
    INTER_SET_DELAY = getattr(settings, 'POKEMON_CARD_INTER_SET_DELAY', 5)
    INTER_BATCH_DELAY = getattr(settings, 'POKEMON_CARD_INTER_BATCH_DELAY', 10)
    MAX_RETRIES_PER_SET = getattr(settings, 'POKEMON_CARD_MAX_RETRIES', 3)
    MEMORY_THRESHOLD = getattr(settings, 'POKEMON_CARD_MEMORY_THRESHOLD', 0.8)

    queryset = PokemonCard.objects.all()
    serializer_class = PokemonCardSerializer
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = PokemonCardFilter
    pagination_class = StandardResultsSetPagination

     # authentication_classes = [SessionAuthentication, BasicAuthentication]
    # permission_classes = [IsAuthenticated]

    # Add authentication and permission classes
    authentication_classes = []
    permission_classes = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)
        self._cache = cache
        self._memory_monitor = self._init_memory_monitor()

    def _init_memory_monitor(self) -> psutil.Process:
        """Initialize memory monitoring."""
        return psutil.Process()

    async def _check_memory_usage(self) -> None:
        """Monitor memory usage and collect garbage if needed."""
        memory_info = self._memory_monitor.memory_info()
        if memory_info.rss > (psutil.virtual_memory().available * self.MEMORY_THRESHOLD):
            gc.collect()
            await asyncio.sleep(0.1)  # Allow other tasks to run

    async def _process_card_data(self, card_data: scraper.CardPriceData) -> dict:
        """Process card data with error handling and validation."""
        try:
            await self._check_memory_usage()
            
            if not all([card_data.card_name, card_data.set_name, card_data.language]):
                raise ValueError("Missing required card data fields")

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
                'last_updated': timezone.now()
            }
        except (ValueError, AttributeError) as e:
            logger.error(f"Data processing error: {str(e)}", exc_info=True)
            raise ValueError(f"Invalid card data: {str(e)}")

    @sync_to_async
    def _save_card_to_db(self, card_dict: dict) -> Optional[PokemonCard]:
        """Save or update card data with transaction management."""
        try:
            with transaction.atomic():
                card, created = PokemonCard.objects.update_or_create(
                    card_name=card_dict['card_name'],
                    set_name=card_dict['set_name'],
                    language=card_dict['language'],
                    rarity=card_dict['rarity'],
                    defaults={k: v for k, v in card_dict.items() 
                            if k not in ['card_name', 'set_name', 'language', 'rarity']}
                )
                return card
        except Exception as e:
            logger.error(f"Database error: {str(e)}", exc_info=True)
            return None

    def _create_card_details(self, search_query: str, set_name: str, language: str) -> scraper.CardDetails:
        """Create and validate CardDetails object."""
        is_set_search = any(term in search_query for term in ["SV", ":"])

        if set_name and not CardSetData.validate_set(set_name):
            raise ValueError(f"Invalid set_name: {set_name}")

        return scraper.CardDetails(
            name="" if is_set_search else search_query,
            set_name=search_query if is_set_search else set_name,
            language=language
        )

    async def _handle_scrape_error(self, error: Exception, scrape_log: ScrapeLog) -> Response:
        """Centralized error handling for scraping operations."""
        logger.error(f"Scraper error: {str(error)}", exc_info=True)
        await sync_to_async(scrape_log.fail)(f"Scraper error: {str(error)}")
        return Response(
            {'error': f'Scraper error: {str(error)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    def _make_safe_cache_key(self, *args) -> str:
        """Create a memcached-safe cache key."""
        # Join args and clean invalid characters
        key_parts = [str(arg).strip() for arg in args if arg]
        safe_key = '_'.join(key_parts)
        # Replace problematic characters
        safe_key = re.sub(r'[\s:/\\?#\[\]@!$&\'()*+,;=]', '_', safe_key)
        # Ensure key is not too long
        if len(safe_key) > 200:
            safe_key = safe_key[:197] + '...'
        return safe_key

    def _clear_cache_sync(self, key: str) -> None:
        """Synchronous cache clearing function."""
        try:
            self._cache.delete(key)
        except Exception as e:
            logger.warning(f"Cache deletion failed for key {key}: {str(e)}")

    async def _clear_cache_async(self, key: str) -> None:
        """Asynchronous wrapper for cache clearing."""
        await sync_to_async(self._clear_cache_sync)(key)

    def _get_ssl_context(self):
        ssl_context = ssl.create_default_context()
        # Enable hostname and certificate verification
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
        # Use modern cipher suites
        ssl_context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20')
        return ssl_context

    @action(detail=False, methods=['get'])
    def scrape_and_save(self, request):
        """Endpoint to scrape and save card data."""
        return async_to_sync(self._scrape_and_save_async)(request)

    async def _scrape_and_save_async(self, request):
        """Asynchronous implementation of scrape_and_save with safe cache keys."""
        search_query = request.query_params.get('searchQuery', '').strip()
        set_name = request.query_params.get('set_name', '').strip()
        language = request.query_params.get('language', 'English')
        user = str(request.user if request.user.is_authenticated else 'anonymous')

        if not search_query:
            return Response(
                {'error': 'Search query is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create safe cache key
        cache_key = self._make_safe_cache_key('scrape', search_query, set_name, language)
        cached_result = self._cache.get(cache_key)
        if cached_result:
            return Response(cached_result)

        scrape_log = await sync_to_async(ScrapeLog.objects.create)(user=user)

        try:
            async with self._request_semaphore:
                card_details = self._create_card_details(search_query, set_name, language)
                logger.info(f"Starting search: {search_query} (Set: {card_details.set_name}, Language: {language})")

                try:
                    profit_data = await scraper.main([card_details])
                except Exception as e:
                    return await self._handle_scrape_error(e, scrape_log)

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
                
                response_data = {
                    'message': f'Successfully processed {len(saved_cards)} cards',
                    'cards': self.serializer_class(saved_cards, many=True).data,
                    'log_id': scrape_log.id
                }
                
                self._cache.set(cache_key, response_data, self.CACHE_TIMEOUT)
                return Response(response_data)

        except ValueError as e:
            logger.error(f"Input validation error: {str(e)}", exc_info=True)
            await sync_to_async(scrape_log.fail)(f"Input validation error: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return await self._handle_scrape_error(e, scrape_log)

    @action(detail=True, methods=['get'])
    def refresh(self, request, pk=None):
        """Refresh data for a specific card."""
        return async_to_sync(self._refresh_async)(request, pk)

    async def _refresh_async(self, request, pk):
        """Asynchronous implementation of refresh with improved error handling."""
        user = str(request.user if request.user.is_authenticated else 'anonymous')
        try:
            card = await sync_to_async(PokemonCard.objects.select_for_update().get)(pk=pk)
            
            # Use safe cache key
            cache_key = self._make_safe_cache_key('card', card.card_name, card.set_name, card.language)

            if not card.product_id:
                return Response(
                    {'error': 'Card missing product ID'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            scrape_log = await sync_to_async(ScrapeLog.objects.create)(user=user)

            async with self._request_semaphore:
                card_details = scraper.CardDetails(
                    name=card.card_name,
                    set_name=card.set_name,
                    language=card.language,
                    product_id=card.product_id
                )

                try:
                    updated_data = await scraper.main([card_details])
                except Exception as e:
                    return await self._handle_scrape_error(e, scrape_log)

                if not updated_data:
                    await sync_to_async(scrape_log.fail)("No updated data found")
                    return Response(
                        {'error': 'No updated data found'},
                        status=status.HTTP_404_NOT_FOUND
                    )

                try:
                    card_dict = await self._process_card_data(updated_data[0])
                    updated_card = await self._save_card_to_db(card_dict)

                    # Invalidate related caches
                    cache_key = f"scrape:{card.card_name}:{card.set_name}:{card.language}"
                    self._cache.delete(cache_key)

                    await sync_to_async(scrape_log.complete)(1, 1)
                    return Response(self.serializer_class(updated_card).data)

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
            return await self._handle_scrape_error(e, scrape_log)

    @action(detail=False, methods=['post'])
    def scrape_all_sets(self, request):
        """Scrape all sets concurrently and save to DB in a background thread."""
        try:
            # Use 'anonymous' as default user
            user = 'anonymous'
            scrape_log = ScrapeLog.objects.create(user=user)

            # Launch scraping as a background thread
            threading.Thread(
                target=lambda: async_to_sync(self._scrape_all_sets_async)(scrape_log.id),
                daemon=True
            ).start()

            return Response({
                'message': 'Bulk scrape started',
                'log_id': scrape_log.id,
                'status': 'processing'
            })
        except Exception as e:
            logger.error(f"Failed to start bulk scrape: {str(e)}", exc_info=True)
            return Response(
                {'error': f'Failed to start scraping: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    async def _scrape_all_sets_async(self, log_id: int):
        """Asynchronous implementation with improved error handling and timeout management."""
        try:
            scrape_log = await sync_to_async(ScrapeLog.objects.get)(id=log_id)
            total_attempted = 0
            total_updated = 0

            async def process_set_batch(sets_batch: List[str], language: str, rarities: List[str]):
                nonlocal total_attempted, total_updated
                for set_name in sets_batch:
                    try:
                        async with self._request_semaphore:
                            await self._check_memory_usage()

                            # Update progress message separately from counts
                            await sync_to_async(scrape_log.update_progress)(
                                message=f"Starting {language} set: {set_name}",
                                success_count=0,
                                failure_count=0
                            )

                            # Configure longer timeouts and SSL context
                            ssl_context = self._get_ssl_context()
                            card_details = scraper.CardDetails(
                                name="",
                                set_name=set_name,
                                language=language
                            )

                            # Add timeout configuration
                            timeout_config = aiohttp.ClientTimeout(
                                total=600,  # 10 minutes total timeout
                                connect=120,  # 2 minutes connection timeout
                                sock_read=120  # 2 minutes read timeout
                            )

                            connector = aiohttp.TCPConnector(
                                ssl=ssl_context,
                                force_close=False,
                                enable_cleanup_closed=True,
                                ttl_dns_cache=300,
                                limit_per_host=5,
                                keepalive_timeout=30  # Send keep-alive packets every 30 seconds
                            )

                            async with aiohttp.ClientSession(
                                timeout=timeout_config,
                                connector=connector
                            ) as session:
                                results = await scraper.main([card_details])
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

                                # Clear cache for this set using the async version
                                cache_key = self._make_safe_cache_key('set', set_name, language)
                                await self._clear_cache_async(cache_key)

                                # Add longer delay between sets
                                await asyncio.sleep(30)  # Increased from 15 to 30 seconds

                                # Update counts after processing
                                await sync_to_async(scrape_log.update_progress)(
                                    message=f"Completed {language} set: {set_name}",
                                    success_count=batch_updated,
                                    failure_count=batch_attempted - batch_updated
                                )
                    except Exception as e:
                        logger.error(f"Error processing {set_name}: {str(e)}", exc_info=True)
                        await sync_to_async(scrape_log.log_error)(
                            f"Error in set {set_name}: {str(e)}"
                        )
                        await sync_to_async(scrape_log.update_progress)(
                            message=f"Failed {language} set: {set_name}",
                            success_count=0,
                            failure_count=1
                        )
                        # Add recovery delay after errors
                        await asyncio.sleep(60)  # 60 seconds delay after error
                        continue

            # Process smaller batches
            for sets, language, rarities in [
                (CardSetData.ENGLISH_SETS[:3], "English", CardSetData.ENGLISH_RARITIES),  # Process fewer sets initially
                (CardSetData.JAPANESE_SETS[:3], "Japanese", CardSetData.JAPANESE_RARITIES)
            ]:
                for i in range(0, len(sets), 1):  # Process one set at a time
                    batch = sets[i:i + 1]
                    await process_set_batch(batch, language, rarities)
                    await asyncio.sleep(60)  # Increased delay between batches

            await sync_to_async(scrape_log.complete)(total_attempted, total_updated)
        except Exception as e:
            logger.error(f"Fatal error in bulk scraping: {str(e)}", exc_info=True)
            await sync_to_async(scrape_log.fail)(str(e))

    def list(self, request, *args, **kwargs):
        """Get cards from database with simplified response."""
        queryset = self.filter_queryset(self.get_queryset())
        
        # If using pagination
        if hasattr(self, 'paginator') and self.paginator and request.query_params.get('page', None):
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
        
        # Return just the serialized data without pagination
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def fetch_card(self, request):
        """Fetch specific card by name with simplified response."""
        # Remove pagination for this endpoint
        self.pagination_class = None
        return self.list(request)

    @action(detail=False, methods=['get'])
    def fetch_set(self, request):
        """Fetch cards by set with simplified response."""
        # Remove pagination for this endpoint
        self.pagination_class = None
        return self.list(request)

    @action(detail=False, methods=['get'])
    def fetch_card_set(self, request):
        """Fetch specific card by name and set with simplified response."""
        # Remove pagination for this endpoint
        self.pagination_class = None
        return self.list(request)

    @action(detail=False, methods=['get'])
    def fetch_card_rarity(self, request):
        """Fetch specific card by name and rarity with simplified response."""
        # Remove pagination for this endpoint
        self.pagination_class = None
        return self.list(request)

    @action(detail=False, methods=['get'])
    def fetch_set_rarity(self, request):
        """Fetch cards by set and rarity with simplified response."""
        # Remove pagination for this endpoint
        self.pagination_class = None
        return self.list(request)
