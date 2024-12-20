# Generated by Django 5.1.4 on 2024-12-19 09:24

import django.core.validators
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('grading_api', '0002_alter_pokemoncard_rarity'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='pokemoncard',
            options={'ordering': ['-profit_potential', 'card_name']},
        ),
        migrations.AddField(
            model_name='pokemoncard',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='pokemoncard',
            name='card_name',
            field=models.CharField(db_index=True, max_length=255),
        ),
        migrations.AlterField(
            model_name='pokemoncard',
            name='language',
            field=models.CharField(choices=[('English', 'English'), ('Japanese', 'Japanese')], default='English', max_length=20),
        ),
        migrations.AlterField(
            model_name='pokemoncard',
            name='price_delta',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name='pokemoncard',
            name='profit_potential',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True, validators=[django.core.validators.MinValueValidator(-100), django.core.validators.MaxValueValidator(1000)]),
        ),
        migrations.AlterField(
            model_name='pokemoncard',
            name='psa_10_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterField(
            model_name='pokemoncard',
            name='set_name',
            field=models.CharField(db_index=True, max_length=255),
        ),
        migrations.AlterField(
            model_name='pokemoncard',
            name='tcgplayer_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AlterUniqueTogether(
            name='pokemoncard',
            unique_together={('card_name', 'set_name', 'language', 'rarity')},
        ),
        migrations.AddIndex(
            model_name='pokemoncard',
            index=models.Index(fields=['card_name', 'set_name', 'language'], name='grading_api_card_na_df2d62_idx'),
        ),
        migrations.AddIndex(
            model_name='pokemoncard',
            index=models.Index(fields=['-profit_potential'], name='grading_api_profit__e5b358_idx'),
        ),
        migrations.AddIndex(
            model_name='pokemoncard',
            index=models.Index(fields=['last_updated'], name='grading_api_last_up_afc17a_idx'),
        ),
    ]
