"""Custom allauth adapters."""

from allauth.account.adapter import DefaultAccountAdapter


class NoPasswordSignupAdapter(DefaultAccountAdapter):
    """Disable allauth's password-based signup.

    Attendees sign in via Google OAuth only, which inherently proves email
    ownership. Blocking password signup closes the "sign up as anyone's
    email to view their calendar / register on their behalf" hole that
    email-based attendee lookups (see views._calendar_email) would otherwise
    expose.

    Organisers are not affected — they go through the custom
    ``/signup/organiser/`` view, which doesn't touch allauth's signup flow.
    """

    def is_open_for_signup(self, request):
        """Reject allauth password signups. Google OAuth still works."""
        return False
