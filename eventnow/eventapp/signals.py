"""allauth signal handlers that keep the attendee session in sync.

EventNow has two parallel identities: the Django auth.User (organisers,
and anyone who signs in) and the per-tenant Attendee record. These
handlers bridge them — when an auth.User signs in, if their email
matches an Attendee, the attendee_* session keys are populated so
"My Calendar" works immediately.
"""

from allauth.account.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .models import Attendee


@receiver(user_logged_in)
def link_attendee_session(sender, request, user, **kwargs):
    """Populate attendee session keys when an auth.User signs in.

    Args:
        sender: The signal sender class (unused).
        request: The HttpRequest for the login; its session is mutated.
        user: The auth.User who just signed in.
        **kwargs: Other signal arguments (unused).
    """
    # Nothing to link without an email or a request to attach the session to.
    if not user.email or request is None:
        return

    # A user may have one Attendee per tenant; pick the most recently
    # updated active one as a sensible default for "My Calendar".
    attendee = (
        Attendee.objects
        .filter(email__iexact=user.email, is_active=True)
        .order_by("-updated_at")
        .first()
    )
    if attendee:
        # Seed the session keys the attendee-facing views read.
        request.session["attendee_id"] = attendee.pk
        request.session["attendee_name"] = (
            attendee.first_name or user.first_name or "there"
        )
        request.session["attendee_tenant_id"] = attendee.tenant_id


@receiver(user_logged_out)
def clear_attendee_session(sender, request, **kwargs):
    """Clear attendee session keys when the auth.User logs out.

    Args:
        sender: The signal sender class (unused).
        request: The HttpRequest for the logout; its session is mutated.
        **kwargs: Other signal arguments (unused).
    """
    if request is None:
        return
    # Remove every attendee-related key so a logged-out browser is clean.
    for key in ("attendee_id", "attendee_name", "attendee_tenant_id"):
        request.session.pop(key, None)