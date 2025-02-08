from rest_framework import serializers
from .models import PokemonCard

class PokemonCardSerializer(serializers.ModelSerializer):
    tcgplayer_last_pulled_formatted = serializers.SerializerMethodField()
    ebay_last_pulled_formatted = serializers.SerializerMethodField()

    class Meta:
        model = PokemonCard
        fields = [
            'id', 'card_name', 'set_name', 'language', 'rarity',
            'tcgplayer_price', 'tcgplayer_last_pulled', 'tcgplayer_last_pulled_formatted',
            'psa_10_price', 'ebay_last_pulled', 'ebay_last_pulled_formatted',
            'price_delta', 'profit_potential', 'last_updated', 'product_id',
        ]

    def get_tcgplayer_last_pulled_formatted(self, obj):
        if obj.tcgplayer_last_pulled:
            return obj.tcgplayer_last_pulled.strftime("%m/%d/%Y %H:%M")
        return None

    def get_ebay_last_pulled_formatted(self, obj):
        if obj.ebay_last_pulled:
            return obj.ebay_last_pulled.strftime("%m/%d/%Y %H:%M")
        return None
