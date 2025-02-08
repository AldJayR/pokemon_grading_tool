# Generated by Django 5.1.4 on 2025-02-08 02:43

import django.core.validators
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='PokemonCard',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('card_name', models.CharField(db_index=True, max_length=255)),
                ('set_name', models.CharField(db_index=True, max_length=255)),
                ('product_id', models.CharField(blank=True, db_index=True, max_length=50, null=True, unique=True)),
                ('card_number', models.CharField(blank=True, max_length=50)),
                ('language', models.CharField(choices=[('English', 'English'), ('Japanese', 'Japanese')], default='English', max_length=20)),
                ('rarity', models.CharField(default='Unknown', max_length=100)),
                ('tcgplayer_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0.00'))])),
                ('tcgplayer_market_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0.00'))])),
                ('tcgplayer_last_pulled', models.DateTimeField(blank=True, null=True)),
                ('psa_10_price', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0.00'))])),
                ('ebay_last_pulled', models.DateTimeField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('last_updated', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['card_name'],
                'indexes': [models.Index(fields=['card_name', 'set_name', 'language'], name='grading_api_card_na_df2d62_idx'), models.Index(fields=['last_updated'], name='grading_api_last_up_afc17a_idx'), models.Index(fields=['product_id'], name='grading_api_product_d9879f_idx'), models.Index(fields=['is_active', 'last_updated'], name='grading_api_is_acti_0421cd_idx')],
                'unique_together': {('card_name', 'set_name', 'language', 'rarity')},
            },
        ),
        migrations.CreateModel(
            name='ScrapeLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.CharField(max_length=255)),
                ('source', models.CharField(choices=[('tcgplayer', 'TCGPlayer'), ('ebay', 'eBay'), ('all', 'All Sources')], default='all', max_length=20)),
                ('status', models.CharField(choices=[('in_progress', 'In Progress'), ('completed', 'Completed'), ('failed', 'Failed'), ('partial', 'Partially Completed')], default='in_progress', max_length=50)),
                ('total_cards_attempted', models.PositiveIntegerField(default=0)),
                ('total_cards_updated', models.PositiveIntegerField(default=0)),
                ('total_cards_failed', models.PositiveIntegerField(default=0)),
                ('error_message', models.TextField(blank=True, null=True)),
                ('retry_count', models.PositiveSmallIntegerField(default=0)),
                ('execution_time', models.DurationField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-started_at'],
                'indexes': [models.Index(fields=['status', 'started_at'], name='grading_api_status_326931_idx'), models.Index(fields=['user', 'started_at'], name='grading_api_user_5b174f_idx')],
            },
        ),
    ]
