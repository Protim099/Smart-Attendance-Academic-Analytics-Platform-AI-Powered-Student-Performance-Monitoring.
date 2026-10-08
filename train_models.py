import json

from django.core.management.base import BaseCommand

from apps.analytics import ml


class Command(BaseCommand):
    help = "Train ML models and refresh student risk predictions."

    def handle(self, *args, **opts):
        out = ml.run_predictions(retrain=True)
        self.stdout.write(json.dumps(out, indent=2, default=str))
