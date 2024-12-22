from rest_framework import serializers
from .models import PokemonCard

class PokemonCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = PokemonCard
        fields = '__all__'