"""
Django settings for pokemon_grading_tool project.

Generated by 'django-admin startproject' using Django 5.1.4.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.1/ref/settings/
"""

import os
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url

# Load environment variables
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv()

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')


# Simplified security settings for development
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = [
    os.environ.get('RAILWAY_PUBLIC_DOMAIN', ''),
    'pokemongradingtool-production.up.railway.app',
]

VERCEL_URL = os.getenv('VERCEL_URL')
if VERCEL_URL: 
    ALLOWED_HOSTS.append(VERCEL_URL)

RAILWAY_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN')
if RAILWAY_URL:
    ALLOWED_HOSTS.append(RAILWAY_URL)

# Installed Apps
INSTALLED_APPS = [
    'rest_framework',
    'grading_api',
    'django_redis',
    'django_filters',
    'corsheaders',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

# Middleware
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Static Files
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# CORS
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    f"https://{host}" for host in ALLOWED_HOSTS if host
]

CSRF_TRUSTED_ORIGINS = [
    f"https://{host}" for host in ALLOWED_HOSTS if host
]

# URLs & Templates
ROOT_URLCONF = 'pokemon_grading_tool.urls'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'pokemon_grading_tool.wsgi.application'
ASGI_APPLICATION = 'pokemon_grading_tool.asgi.application'

# Update Redis Cache Configuration
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
        'OPTIONS': {
            'db': 0,
            'retry_on_timeout': True,
            'socket_timeout': 5,
            'socket_connect_timeout': 5,
            'connection_pool_kwargs': {
                'max_connections': 50,
                'timeout': 20
            }
        }
    }
}

# Remove these as they're not needed
# REDIS_TIMEOUT = 3600
# REDIS_CONNECT_RETRY = True
# REDIS_CONNECTION_POOL_CLASS = 'redis.BlockingConnectionPool'

POKEMON_CARD_SETTINGS = {
    # Cache timeout in seconds (1 hour default)
    'POKEMON_CARD_CACHE_TIMEOUT': 3600,
    
    # Request limits
    'POKEMON_CARD_MAX_CONCURRENT_REQUESTS': 1,  # Reduce from 2
    
    # Batch processing
    'POKEMON_CARD_BATCH_SIZE': 1,  # Reduce from 2
    
    # Delays (in seconds)
    'POKEMON_CARD_INTER_SET_DELAY': 10,  # Increase from 5
    'POKEMON_CARD_INTER_BATCH_DELAY': 15,  # Increase from 10
    
    # Retries
    'POKEMON_CARD_MAX_RETRIES': 3,
    
    # Memory management
    'POKEMON_CARD_MEMORY_THRESHOLD': 0.8,  # 80% threshold for garbage collection
    
    # Pagination
    'POKEMON_CARD_PAGE_SIZE': 100,
    'POKEMON_CARD_MAX_PAGE_SIZE': 1000,
}

# Add these to POKEMON_CARD_SETTINGS
POKEMON_CARD_SETTINGS.update({
    'POKEMON_CARD_ERROR_RETRY_DELAY': 30,  # seconds
    'POKEMON_CARD_MAX_ERRORS_BEFORE_ABORT': 5,
    'POKEMON_CARD_ERROR_COOLDOWN': 300,  # 5 minutes
    'POKEMON_CARD_MAX_CONCURRENT_REQUESTS': 1,  # Reduce concurrency
    'POKEMON_CARD_BATCH_SIZE': 1,  # Process one set at a time
    'POKEMON_CARD_INTER_SET_DELAY': 15,  # Longer delays between sets
    'POKEMON_CARD_INTER_BATCH_DELAY': 20,  # Longer delays between batches
    'POKEMON_CARD_ERROR_RETRY_DELAY': 30,
    'POKEMON_CARD_MAX_RETRIES': 3,
})

locals().update(POKEMON_CARD_SETTINGS)


# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': [],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': POKEMON_CARD_SETTINGS['POKEMON_CARD_PAGE_SIZE'],
}

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}

# Database Configuration
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': os.environ["PGDATABASE"],
        'USER': os.environ["PGUSER"],
        'PASSWORD': os.environ["PGPASSWORD"],
        'HOST': os.environ["PGHOST"],  
        'PORT': os.environ["PGPORT"],    
    }
}

CACHE_MIDDLEWARE_SECONDS = 3600
CACHE_MIDDLEWARE_KEY_PREFIX = 'pokemon_cards'

# Password Validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Security settings (only applied in production)
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

# Content Security Policy
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Cookie settings
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'
CSRF_COOKIE_SAMESITE = 'Strict'


# Default Auto Field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
