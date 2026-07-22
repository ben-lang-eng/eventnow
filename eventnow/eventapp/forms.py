"""Django forms for EventNow.

Covers organiser-facing model forms (events, sessions, speakers) and
two plain forms used for attendee registration and organiser signup.
"""

from django import forms
from django.utils import timezone
from .models import Event, Session, SessionSpeaker
from allauth.account.forms import SignupForm


class EventForm(forms.ModelForm):
    """Create/edit form for an Event.

    Tenant and organiser are deliberately NOT form fields — they're set
    server-side in the view from request.user.profile, so an organiser
    cannot reassign an event to another tenant by tampering with POST data.
    """

    class Meta:
        model = Event
        fields = [
            "title",
            "description",
            "address",
            "city",
            "state",
            "postcode",
            "country",
            "latitude",
            "longitude",
            "cost",
            "start_time",
            "end_time",
            "max_attendees",
            "status",
            "event_website_slug",
            "banner_image",
            "banner_image_alt",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
            "address": forms.TextInput(attrs={"class": "form-control"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "state": forms.TextInput(attrs={"class": "form-control"}),
            "postcode": forms.TextInput(attrs={"class": "form-control"}),
            "country": forms.TextInput(attrs={"class": "form-control"}),
            "latitude": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.000001"}
            ),
            "longitude": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.000001"}
            ),
            "cost": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
            "start_time": forms.DateTimeInput(
                attrs={"class": "form-control", "type": "datetime-local"}
            ),
            "end_time": forms.DateTimeInput(
                attrs={"class": "form-control", "type": "datetime-local"}
            ),
            "max_attendees": forms.NumberInput(
                attrs={"class": "form-control", "min": "1"}
            ),
            "status": forms.Select(attrs={"class": "form-select"}),
            "event_website_slug": forms.TextInput(attrs={"class": "form-control"}),
            "banner_image": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "banner_image_alt": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. A red and black border collie on a lawn waiting for some pats.",
                }
            ),
        }

    def clean(self):
        """Validate cross-field constraints.

        Returns:
            dict: The cleaned data.

        Raises:
            forms.ValidationError: If end_time is not strictly after start_time.
        """
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")
        if start and end and end <= start:
            raise forms.ValidationError("End time must be after start time.")
        return cleaned

    def clean_cost(self):
        """Reject a negative event cost.

        Returns:
            Decimal | None: The cleaned cost value.

        Raises:
            forms.ValidationError: If cost is negative.
        """
        cost = self.cleaned_data.get("cost")
        if cost is not None and cost < 0:
            raise forms.ValidationError("Cost cannot be negative.")
        return cost


class SessionForm(forms.ModelForm):
    """Form for creating and editing a Session.

    Args:
        event: Optional parent Event used to set date-range and capacity
            constraints on the widgets and to validate submitted values
            server-side. When omitted and a saved instance exists, the event
            is resolved from that instance.
    """

    def __init__(self, *args, event=None, **kwargs):
        """Initialise the form; apply min/max constraints when event is known."""
        super().__init__(*args, **kwargs)
        # Resolve parent event: explicit kwarg takes precedence, then instance.
        self._event = event or (
            self.instance.event if self.instance and self.instance.pk else None
        )
        if self._event:
            ev = self._event
            fmt = "%Y-%m-%dT%H:%M"
            ev_start = timezone.localtime(ev.start_time).strftime(fmt)
            ev_end = timezone.localtime(ev.end_time).strftime(fmt)
            # HTML5 min/max give browser-level feedback before the form is submitted.
            self.fields["start_time"].widget.attrs.update(
                {"min": ev_start, "max": ev_end}
            )
            self.fields["end_time"].widget.attrs.update(
                {"min": ev_start, "max": ev_end}
            )
            self.fields["max_capacity"].widget.attrs["max"] = ev.max_attendees

    class Meta:
        model = Session
        fields = [
            "title",
            "description",
            "room_name",
            "start_time",
            "end_time",
            "max_capacity",
            "status",
            "image",
            "image_alt",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "room_name": forms.TextInput(attrs={"class": "form-control"}),
            "start_time": forms.DateTimeInput(
                attrs={"class": "form-control", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "end_time": forms.DateTimeInput(
                attrs={"class": "form-control", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "max_capacity": forms.NumberInput(
                attrs={"class": "form-control", "min": 1}
            ),
            "status": forms.Select(attrs={"class": "form-select"}),
            "image": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "image_alt": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean(self):
        """Validate start < end and that both fall within the parent event's range."""
        cleaned = super().clean()
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")

        if start and end and start >= end:
            self.add_error("end_time", "End time must be after start time.")

        ev = self._event
        if ev:
            if start and start < ev.start_time:
                self.add_error(
                    "start_time",
                    f"Must be on or after the event start "
                    f"({timezone.localtime(ev.start_time):%d %b %Y, %H:%M}).",
                )
            if end and end > ev.end_time:
                self.add_error(
                    "end_time",
                    f"Must be on or before the event end "
                    f"({timezone.localtime(ev.end_time):%d %b %Y, %H:%M}).",
                )
            capacity = cleaned.get("max_capacity")
            if capacity and capacity > ev.max_attendees:
                self.add_error(
                    "max_capacity",
                    f"Cannot exceed the event's total capacity ({ev.max_attendees}).",
                )

        return cleaned


class AttendeeRegistrationForm(forms.Form):
    """Captures attendee details when registering for an event.

    Not a ModelForm — the view does the get_or_create on Attendee itself,
    because Attendee is keyed on (tenant, email) and the tenant comes from
    the event being registered for, not from form input.
    """

    first_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Jane"}),
    )
    last_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Doe"}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={"class": "form-control", "placeholder": "jane@example.com"}
        ),
    )
    phone = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "+61 4xx xxx xxx (optional)"}
        ),
    )

    def __init__(self, *args, **kwargs):
        """Build the form, optionally locking the email field.

        Args:
            *args: Standard form positional args (data, files, ...).
            **kwargs: Standard form kwargs, plus:
                lock_email (bool): If True, render the email field
                    read-only — used when the registrant is already
                    signed in and their email must not change.
        """
        lock_email = kwargs.pop("lock_email", False)
        super().__init__(*args, **kwargs)
        if lock_email:
            self.fields["email"].widget.attrs["readonly"] = True
            self.fields["email"].help_text = "Linked to your signed-in account."


class AttendeeSignupForm(SignupForm):
    """Allauth signup form extended with name fields for attendees."""

    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Jane"}),
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Doe"}),
    )

    def signup(self, request, user):
        """Persist the name fields onto the new User (called by allauth)."""
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.save(update_fields=["first_name", "last_name"])


class OrganiserSignupForm(forms.Form):
    """Collects everything needed to create an organiser in one step.

    The view uses this to create an auth.User + Tenant + UserProfile
    together, so a new organiser lands with a ready-to-use workspace.
    """

    first_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Jane"}),
    )
    last_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Doe"}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={"class": "form-control", "placeholder": "jane@example.com"}
        ),
    )
    password = forms.CharField(
        min_length=8,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        help_text="At least 8 characters.",
    )
    workspace_name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. UQ Conferences"}
        ),
        help_text="Shown to attendees on event pages.",
    )
    workspace_slug = forms.SlugField(
        max_length=100,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "uq-conferences"}
        ),
        help_text="Lowercase letters, numbers, and dashes only.",
    )


class SessionSpeakerForm(forms.ModelForm):
    """Add an external speaker to a session.

    Only display_name and bio are exposed — the session FK is set in the
    view, and the optional user FK (for internal speakers) is left null.
    """

    class Meta:
        model = SessionSpeaker
        fields = ["display_name", "bio"]
        widgets = {
            "display_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. Dr Jane Smith",
                }
            ),
            "bio": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Short biography shown on the session page.",
                }
            ),
        }
