"""Database models for EventNow — a multi-tenant event-management platform.

Tenancy: every domain object ultimately belongs to a Tenant. Organisers
are auth.Users with a UserProfile pointing at a Tenant; attendees are
separate per-tenant Attendee records keyed on (tenant, email).
"""

from django.conf import settings
from django.db import models


class Tenant(models.Model):
    """A SaaS subscriber organisation — owns events, users and attendees."""

    SUBSCRIPTION_PLANS = [
        ("free", "Free"),
        ("starter", "Starter"),
        ("pro", "Pro"),
    ]
    STATUS_CHOICES = [
        ("active", "Active"),
        ("archived", "Archived"),   # soft-deleted
        ("suspended", "Suspended"),
    ]

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    contact_email = models.EmailField()
    subscription_plan = models.CharField(
        max_length=50, choices=SUBSCRIPTION_PLANS, default="free"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        """Return the tenant's display name."""
        return self.name


class UserProfile(models.Model):
    """One-to-one extension of auth.User that ties a user to a Tenant.

    The presence of a UserProfile is what marks an auth.User as an
    organiser. Accessed in views/templates as ``request.user.profile``.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="users")
    phone = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # A user belongs to one tenant; guard against duplicate rows.
        unique_together = [("tenant", "user")]

    def __str__(self):
        """Return 'Full Name (Tenant)' for admin readability."""
        return f"{self.user.get_full_name() or self.user.username} ({self.tenant.name})"


class Attendee(models.Model):
    """A person who registers for events, scoped to a single Tenant.

    The same email can have one Attendee row per tenant — so a user who
    attends events run by different organisers has multiple Attendee
    records. Created lazily on first event registration.
    """

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="attendees")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One attendee record per email per tenant.
        unique_together = [("tenant", "email")]

    def __str__(self):
        """Return 'First Last <email>'."""
        return f"{self.first_name} {self.last_name} <{self.email}>"


class Event(models.Model):
    """An event organised by a tenant user.

    Soft-deleted via the 'archived' status rather than row deletion.
    Location is stored both as text fields and optional lat/long for
    the Google Maps embed.
    """

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),   # visible to the public
        ("archived", "Archived"),     # soft-deleted
        ("cancelled", "Cancelled"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="events")
    # PROTECT: an organiser with events cannot be silently removed.
    organiser = models.ForeignKey(UserProfile, on_delete=models.PROTECT, related_name="events")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    max_attendees = models.PositiveIntegerField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="draft")
    event_website_slug = models.SlugField(max_length=150, blank=True)
    banner_image = models.ImageField(upload_to="event_banners/", null=True, blank=True)
    banner_image_alt = models.CharField(
        max_length=255,
        blank=True,
        help_text="Brief description of the banner image for screen readers.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        """Return the event title."""
        return self.title

    @property
    def location_query(self):
        """Return address, city and state joined for use as a Maps 'q=' value.

        Skips empty fields so we don't get stray commas. Used by the event
        detail template with the ``|urlencode`` filter so user-supplied
        address text can't break out of the iframe src attribute.
        """
        return ", ".join(p for p in (self.address, self.city, self.state) if p)


class Session(models.Model):
    """A scheduled session within an Event (talk, workshop, activity)."""

    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("ongoing", "Ongoing"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),   # soft-deleted
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="sessions")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    room_name = models.CharField(max_length=100, blank=True)
    image = models.ImageField(upload_to="sessions/", blank=True, null=True)
    image_alt = models.CharField(
        max_length=255,
        blank=True,
        help_text="Brief description of the session image for screen readers.",
    )
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    max_capacity = models.PositiveIntegerField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="scheduled")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Sessions list chronologically wherever they're shown.
        ordering = ["start_time"]

    def __str__(self):
        """Return 'Event title — Session title'."""
        return f"{self.event.title} — {self.title}"


class SessionSpeaker(models.Model):
    """A speaker presenting a session.

    ``user`` is null for external speakers (not a tenant user); for
    internal speakers it points at the presenting UserProfile.
    """

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="speakers")
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,   # keep the speaker row if the profile is removed
        null=True,
        blank=True,
        related_name="speaking_sessions",
    )
    display_name = models.CharField(max_length=255)
    bio = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # A given internal user is listed at most once per session.
        unique_together = [("session", "user")]

    def __str__(self):
        """Return the speaker's display name."""
        return self.display_name


class EventRegistration(models.Model):
    """An attendee's registration for an event."""

    STATUS_CHOICES = [
        ("confirmed", "Confirmed"),
        ("waitlisted", "Waitlisted"),
        ("cancelled", "Cancelled"),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registrations")
    attendee = models.ForeignKey(Attendee, on_delete=models.CASCADE, related_name="event_registrations")
    registration_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="confirmed")
    registered_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)   # set on door check-in
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # An attendee registers for a given event at most once.
        unique_together = [("event", "attendee")]

    def __str__(self):
        """Return 'attendee → event title'."""
        return f"{self.attendee} → {self.event.title}"


class SessionRegistration(models.Model):
    """An attendee's registration for a session, under their event registration.

    Linked to EventRegistration (not Attendee directly) so a session
    booking only exists if the attendee is registered for the parent event.
    """

    STATUS_CHOICES = [
        ("confirmed", "Confirmed"),
        ("waitlisted", "Waitlisted"),   # session was at capacity
        ("cancelled", "Cancelled"),
    ]

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="registrations")
    event_registration = models.ForeignKey(
        EventRegistration, on_delete=models.CASCADE, related_name="session_registrations"
    )
    registration_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="confirmed")
    registered_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One session booking per event registration.
        unique_together = [("session", "event_registration")]

    def __str__(self):
        """Return 'attendee → session title'."""
        return f"{self.event_registration.attendee} → {self.session.title}"


