from django.urls import path, include
from rest_framework import routers
from .views import PokemonCardViewSet

router = routers.DefaultRouter()
router.register(r'cards', PokemonCardViewSet)


urlpatterns = [
    path('', include(router.urls)),
]