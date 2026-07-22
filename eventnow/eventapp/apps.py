"""App configuration for the eventapp Django application."""

from django.apps import AppConfig


class EventappConfig(AppConfig):
    """Django AppConfig for eventapp.

    Connects the allauth login/logout signal handlers on startup so
    attendee session keys stay in sync with auth.User sessions.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "eventapp"

    def ready(self):
        """Import the signals module so its @receiver handlers register.

        Called once by Django when the app registry is fully loaded.
        """
        from . import signals  # noqa: F401  (imported for its side effects)