"""
WSGI config for eventnow project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

from eventnow.telemetry import setup_telemetry

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eventnow.settings')

# Must run before get_wsgi_application(): the OTel Django instrumentation
# inserts its middleware into settings.MIDDLEWARE, and the handler snapshots
# the middleware chain when it is built. No-op unless OTEL_* env vars are set.
setup_telemetry()

application = get_wsgi_application()
