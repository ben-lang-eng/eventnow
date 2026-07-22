"""Views for EventNow — event/session CRUD, attendee/session registration,
calendar JSON feeds, AI description polishing, and the organiser signup flow.

All views are function-based, following the applied-class pattern:
@login_required guards organiser-only actions, @require_POST guards mutations.
Multi-tenancy is enforced by scoping every organiser query through
_user_tenant(request).
"""

import logging

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.utils.dateparse import parse_datetime
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

from .ai import polish_event_description as ai_polish_description
from .forms import (
    AttendeeRegistrationForm,
    EventForm,
    OrganiserSignupForm,
    SessionForm,
    SessionSpeakerForm,
)
from .models import (
    Attendee,
    Event,
    EventRegistration,
    Session,
    SessionRegistration,
    SessionSpeaker,
    Tenant,
    UserProfile,
)


# --- Helpers ----------------------------------------------------------------


def _attendee_event_registration(request, event):
    """Return the signed-in attendee's confirmed EventRegistration for an event.

    Args:
        request: The HttpRequest; reads ``attendee_id`` from the session.
        event: The Event to look up the registration against.

    Returns:
        EventRegistration | None: The confirmed registration, or None if the
        visitor has no attendee session or is not registered for this event.
    """
    attendee_id = request.session.get("attendee_id")
    if not attendee_id:
        return None
    return EventRegistration.objects.filter(
        attendee_id=attendee_id,
        event=event,
        registration_status="confirmed",
    ).first()


def _user_tenant(request):
    """Return the Tenant owned by the authenticated organiser.

    Args:
        request: The HttpRequest; ``request.user`` may be anonymous.

    Returns:
        Tenant | None: The user's tenant, or None if the user has no
        UserProfile (i.e. they are an attendee, not an organiser).
    """
    profile = getattr(request.user, "profile", None)
    return profile.tenant if profile else None


def _calendar_email(request):
    """Resolve the email to use for personal-calendar lookups.

    Works whether the visitor is a signed-in auth.User (organiser, or attendee
    who signed in with a password / Google) or an attendee-session visitor.

    Args:
        request: The HttpRequest.

    Returns:
        str | None: The visitor's email address, or None if it can't be
        resolved.
    """
    # Prefer the authenticated user's email when available.
    if request.user.is_authenticated and request.user.email:
        return request.user.email
    # Fall back to the attendee row referenced by the session.
    attendee_id = request.session.get("attendee_id")
    if attendee_id:
        a = Attendee.objects.filter(pk=attendee_id).first()
        if a:
            return a.email
    return None


# --- Public pages -----------------------------------------------------------


def index(request):
    """Render the public landing page.

    Args:
        request: The HttpRequest.

    Returns:
        HttpResponse: The rendered landing_page.html.
    """
    return render(request, "landing_page.html")


def privacy(request):
    """Render the static privacy policy page.

    Args:
        request: The HttpRequest.

    Returns:
        HttpResponse: The rendered privacy_policy.html.
    """
    return render(request, "privacy_policy.html")


def terms(request):
    """Render the static terms of service page.

    Args:
        request: The HttpRequest.

    Returns:
        HttpResponse: The rendered terms_of_service.html.
    """
    return render(request, "terms_of_service.html")


def calendar(request):
    """Render the public all-events calendar page.

    The page itself is a FullCalendar shell; it fetches its data from
    ``event_calendar_json``.

    Args:
        request: The HttpRequest.

    Returns:
        HttpResponse: The rendered calendar.html.
    """
    return render(request, "calendar.html")


# --- Event CRUD ------------------------------------------------


def event_list(request):
    """List published events with keyword search and pagination.

    Args:
        request: The HttpRequest; optional ``?q=`` search term and ``?page=``.

    Returns:
        HttpResponse: The rendered events_page.html with a page of events.
    """
    query = request.GET.get("q", "").strip()
    tenant = _user_tenant(request)

    # Public sees published events only. Organisers of a tenant additionally
    # see their own tenant's drafts and archived events — otherwise an event
    # saved from the create form as a draft is orphaned (no UI ever lists it).
    if tenant is not None:
        events = Event.objects.filter(
            Q(status="published")
            | Q(tenant=tenant, status__in=["draft", "archived"])
        ).order_by("start_time")
    else:
        events = Event.objects.filter(status="published").order_by("start_time")

    # Keyword search across title, description and city.
    if query:
        events = events.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(city__icontains=query)
        ).distinct()

    # 9 cards per page (3x3 grid in the template).
    paginator = Paginator(events, 9)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Which events on this page is the signed-in visitor registered for?
    registered_event_ids = set()
    email = _calendar_email(request)
    if email:
        registered_event_ids = set(
            EventRegistration.objects.filter(
                attendee__email__iexact=email,
                registration_status="confirmed",
            ).values_list("event_id", flat=True)
        )

    return render(
        request,
        "events_page.html",
        {
            "events": page_obj.object_list,
            "page_obj": page_obj,
            "is_paginated": page_obj.has_other_pages(),
            "query": query,
            "registered_event_ids": registered_event_ids,
        },
    )


def event_detail(request, pk):
    """Show one event with its sessions and live capacity figures.

    Args:
        request: The HttpRequest.
        pk: PK of the Event to display.

    Returns:
        HttpResponse: The rendered individual_event_page.html.

    Raises:
        Http404: If no event with that pk exists, or if the event is not
            published and the visitor is not an organiser of its tenant.
    """
    event = get_object_or_404(Event, pk=pk)

    # Drafts, archived and cancelled events are only visible to organisers of
    # the owning tenant — never to the public — so an event's URL doesn't leak
    # its existence or contents before it's ready.
    tenant = _user_tenant(request)
    is_owner = tenant is not None and tenant.id == event.tenant_id
    if event.status != "published" and not is_owner:
        raise Http404("Event not available.")

    # Owners see every session (including cancelled ones); the public sees
    # only non-cancelled sessions.
    if is_owner:
        session_qs = event.sessions.all()
    else:
        session_qs = event.sessions.exclude(status="cancelled")

    # Annotate each session with its confirmed headcount for the capacity UI.
    sessions = session_qs.annotate(
        confirmed_count=Count(
            "registrations",
            filter=Q(registrations__registration_status="confirmed"),
        )
    ).order_by("start_time")

    # Event-level capacity for the doughnut chart on the page.
    event_confirmed = event.registrations.filter(
        registration_status="confirmed"
    ).count()
    event_spots_left = max(event.max_attendees - event_confirmed, 0)

    # Is the signed-in attendee registered for this event?
    attendee_registration = _attendee_event_registration(request, event)
    # Which sessions of this event is the attendee registered for?
    registered_session_ids = set()
    if attendee_registration:
        registered_session_ids = set(
            SessionRegistration.objects.filter(
                event_registration=attendee_registration,
                registration_status__in=["confirmed", "waitlisted"],
            ).values_list("session_id", flat=True)
        )
    return render(
        request,
        "individual_event_page.html",
        {
            "event": event,
            "sessions": sessions,
            "event_confirmed": event_confirmed,
            "event_spots_left": event_spots_left,
            "attendee_registration": attendee_registration,
            "registered_session_ids": registered_session_ids,
        },
    )


@login_required
def event_create(request):
    """Create an event for the organiser's tenant.

    Args:
        request: The HttpRequest; POST data is an EventForm (with FILES for
        the banner image). ``action=draft`` saves as a draft, otherwise the
        event is published.

    Returns:
        HttpResponse: Redirect to the new event on success, otherwise the
        re-rendered event_creation.html form.
    """
    tenant = _user_tenant(request)
    if tenant is None:
        messages.error(request, "You need a workspace profile to create events.")
        return redirect("events")

    if request.method == "POST":
        form = EventForm(request.POST, request.FILES)
        if form.is_valid():
            event = form.save(commit=False)
            # Stamp ownership before saving — never trust the form for this.
            event.tenant = tenant
            event.organiser = request.user.profile
            event.status = (
                "draft" if request.POST.get("action") == "draft" else "published"
            )
            event.save()
            messages.success(
                request,
                f"Event '{event.title}' "
                f"{'saved as draft' if event.status == 'draft' else 'published'}.",
            )
            return redirect("event_detail", pk=event.pk)
    else:
        form = EventForm()

    return render(
        request,
        "event_creation.html",
        {
            "form": form,
            "page_title": "Create a new event",
            "button_text": "Create Event",
        },
    )


@login_required
def event_edit(request, pk):
    """Edit an existing event owned by the organiser's tenant.

    Args:
        request: The HttpRequest; POST data is an EventForm. ``action`` may be
        ``draft`` or ``publish`` to change status; if absent the status is
        left untouched.
        pk: PK of the Event to edit.

    Returns:
        HttpResponse: Redirect to the event on success, otherwise the
        re-rendered event_creation.html form.

    Raises:
        Http404: If the event does not belong to the organiser's tenant.
    """
    tenant = _user_tenant(request)
    if tenant is None:
        messages.error(request, "You need a workspace profile to edit events.")
        return redirect("events")

    # Scoping by tenant here is the multi-tenant access check.
    event = get_object_or_404(Event, pk=pk, tenant=tenant)

    if request.method == "POST":
        form = EventForm(request.POST, request.FILES, instance=event)
        if form.is_valid():
            event = form.save(commit=False)
            action = request.POST.get("action")
            if action == "draft":
                event.status = "draft"
            elif action == "publish":
                event.status = "published"
            # If action is missing/unknown, keep the existing status untouched.
            event.save()
            messages.success(
                request,
                f"Event '{event.title}' updated"
                + (
                    f" and {'saved as draft' if event.status == 'draft' else 'published'}."
                    if action in {"draft", "publish"}
                    else "."
                ),
            )
            return redirect("event_detail", pk=event.pk)
    else:
        form = EventForm(instance=event)

    return render(
        request,
        "event_creation.html",
        {
            "form": form,
            "page_title": f"Edit: {event.title}",
            "button_text": "Save Changes",
            "event": event,
        },
    )


@login_required
@require_POST
def event_archive(request, pk):
    """Soft-delete an event by flipping its status to 'archived'.

    Args:
        request: The HttpRequest (POST only).
        pk: PK of the Event to archive.

    Returns:
        HttpResponse: Redirect to the events list.

    Raises:
        Http404: If the event does not belong to the organiser's tenant.
    """
    tenant = _user_tenant(request)
    event = get_object_or_404(Event, pk=pk, tenant=tenant)
    event.status = "archived"
    event.save(update_fields=["status", "updated_at"])
    messages.success(request, f"'{event.title}' has been archived.")
    return redirect("events")


# --- Event registration -----------------------------------------------------


@login_required
def event_register(request, pk):
    """Register the signed-in user for an event.

    Requires authentication — attendees sign in with a password or Google
    before registering (the magic link has been removed).

    Args:
        request: The HttpRequest; POST data is an AttendeeRegistrationForm
        with the email field locked to the signed-in user's email.
        pk: PK of the published Event to register for.

    Returns:
        HttpResponse: Redirect to My Calendar on success, otherwise the
        re-rendered event_register.html form.

    Raises:
        Http404: If no published event with that pk exists.
    """
    event = get_object_or_404(Event, pk=pk, status="published")

    if request.method == "POST":
        form = AttendeeRegistrationForm(request.POST, lock_email=True)
        if form.is_valid():
            # The email always comes from the authenticated user, never the form.
            email = request.user.email.lower()
            # Attendee rows are per-tenant, so get_or_create is scoped by tenant.
            attendee, created = Attendee.objects.get_or_create(
                tenant=event.tenant,
                email=email,
                defaults={
                    "first_name": form.cleaned_data["first_name"],
                    "last_name": form.cleaned_data["last_name"],
                    "phone": form.cleaned_data["phone"],
                },
            )
            # Existing attendee: refresh their details from the form.
            if not created:
                attendee.first_name = form.cleaned_data["first_name"]
                attendee.last_name = form.cleaned_data["last_name"]
                if form.cleaned_data["phone"]:
                    attendee.phone = form.cleaned_data["phone"]
                attendee.save()

            registration, reg_created = EventRegistration.objects.get_or_create(
                event=event,
                attendee=attendee,
            )
            # Re-activate a previously cancelled registration.
            if not reg_created and registration.registration_status == "cancelled":
                registration.registration_status = "confirmed"
                registration.save()

            # Seed the attendee session so session bookings work immediately.
            request.session["attendee_id"] = attendee.pk
            request.session["attendee_name"] = attendee.first_name
            request.session["attendee_tenant_id"] = attendee.tenant_id

            verb = "already registered" if not reg_created else "registered"
            messages.success(
                request,
                f"You're {verb} for '{event.title}'. View it in My Calendar.",
            )
            return redirect("attendee_calendar")
    else:
        # Pre-fill from the signed-in user's account details.
        form = AttendeeRegistrationForm(
            initial={
                "first_name": request.user.first_name,
                "last_name": request.user.last_name,
                "email": request.user.email,
            },
            lock_email=True,
        )

    return render(request, "event_register.html", {"form": form, "event": event})


@require_POST
def event_unregister(request, pk):
    """Cancel the signed-in attendee's registration for an event.

    Also cancels their session registrations under this event — an attendee
    can't attend sessions for an event they've left.

    Args:
        request: The HttpRequest (POST only).
        pk: PK of the Event to unregister from.

    Returns:
        HttpResponse: Redirect to the attendee's calendar.
    """
    event = get_object_or_404(Event, pk=pk)
    event_reg = _attendee_event_registration(request, event)
    if event_reg is None:
        messages.error(request, "You're not registered for this event.")
        return redirect("event_detail", pk=event.pk)

    # Cancel session bookings tied to this event registration first.
    SessionRegistration.objects.filter(
        event_registration=event_reg,
        registration_status__in=["confirmed", "waitlisted"],
    ).update(registration_status="cancelled")

    event_reg.registration_status = "cancelled"
    event_reg.save(update_fields=["registration_status"])
    messages.success(request, f"You've unregistered from '{event.title}'.")
    return redirect("attendee_calendar")


# --- Session CRUD ----------------------------------------------


def session_detail(request, pk):
    """Show one session with its live capacity figures.

    Args:
        request: The HttpRequest.
        pk: PK of the Session to display.

    Returns:
        HttpResponse: The rendered individual_session_page.html.

    Raises:
        Http404: If no session with that pk exists, or if the session belongs
            to a non-published event and the visitor is not an organiser of
            its tenant.
    """
    session = get_object_or_404(Session, pk=pk)

    # Sessions of draft / archived / cancelled events are private to the
    # owning tenant — same rule as event_detail, applied one level down.
    tenant = _user_tenant(request)
    is_owner = tenant is not None and tenant.id == session.event.tenant_id
    if session.event.status != "published" and not is_owner:
        raise Http404("Session not available.")

    confirmed_count = session.registrations.filter(
        registration_status="confirmed"
    ).count()

    # Is the signed-in attendee registered for this session?
    attendee_session_registration = None
    event_reg = _attendee_event_registration(request, session.event)
    if event_reg:
        attendee_session_registration = SessionRegistration.objects.filter(
            session=session,
            event_registration=event_reg,
            registration_status__in=["confirmed", "waitlisted"],
        ).first()

    return render(
        request,
        "individual_session_page.html",
        {
            "session": session,
            "confirmed_count": confirmed_count,
            "spots_left": max(session.max_capacity - confirmed_count, 0),
            "attendee_session_registration": attendee_session_registration,
        },
    )


@login_required
def session_create(request, event_pk):
    """Add a session to an event the organiser's tenant owns.

    Args:
        request: The HttpRequest; POST data is a SessionForm (with FILES for
        the session image).
        event_pk: PK of the parent Event.

    Returns:
        HttpResponse: Redirect to the parent event on success, otherwise the
        re-rendered session_form.html.

    Raises:
        Http404: If the event does not belong to the organiser's tenant.
    """
    tenant = _user_tenant(request)
    event = get_object_or_404(Event, pk=event_pk, tenant=tenant)

    if request.method == "POST":
        form = SessionForm(request.POST, request.FILES, event=event)
        if form.is_valid():
            session = form.save(commit=False)
            session.event = event  # Attach to the parent before saving.
            session.save()
            messages.success(request, f"Session '{session.title}' created.")
            return redirect("event_detail", pk=event.pk)
    else:
        form = SessionForm(event=event)

    return render(
        request,
        "session_form.html",
        {
            "form": form,
            "event": event,
            "page_title": f"Add session to {event.title}",
            "button_text": "Create session",
        },
    )


@login_required
def session_edit(request, pk):
    """Edit a session owned by the organiser's tenant.

    The session_form.html template also renders the HTMX speaker-management
    section when editing (it needs a saved session to attach speakers to).

    Args:
        request: The HttpRequest; POST data is a SessionForm (with FILES).
        pk: PK of the Session to edit.

    Returns:
        HttpResponse: Redirect to the parent event on success, otherwise the
        re-rendered session_form.html.

    Raises:
        Http404: If the session does not belong to the organiser's tenant.
    """
    tenant = _user_tenant(request)
    session = get_object_or_404(Session, pk=pk, event__tenant=tenant)

    if request.method == "POST":
        form = SessionForm(request.POST, request.FILES, instance=session)
        if form.is_valid():
            session = form.save()
            messages.success(request, f"Session '{session.title}' updated.")
            return redirect("event_detail", pk=session.event.pk)
    else:
        form = SessionForm(instance=session)

    return render(
        request,
        "session_form.html",
        {
            "form": form,
            "event": session.event,
            "session": session,
            "page_title": "Edit session",
            "button_text": "Save changes",
        },
    )


@login_required
@require_POST
def session_archive(request, pk):
    """Soft-delete a session by flipping its status to 'cancelled'.

    Args:
        request: The HttpRequest (POST only).
        pk: PK of the Session to cancel.

    Returns:
        HttpResponse: Redirect to the parent event.

    Raises:
        Http404: If the session does not belong to the organiser's tenant.
    """
    tenant = _user_tenant(request)
    session = get_object_or_404(Session, pk=pk, event__tenant=tenant)
    session.status = "cancelled"
    session.save(update_fields=["status", "updated_at"])
    messages.success(request, f"Session '{session.title}' has been cancelled.")
    return redirect("event_detail", pk=session.event.pk)


# --- Session registration & check-in ---------------------------


@require_POST
def session_register(request, pk):
    """Register the signed-in attendee for a session.

    Enforces session capacity: when a session is full the attendee is placed
    on the waitlist instead of being confirmed.

    Args:
        request: The HttpRequest (POST only).
        pk: PK of the scheduled Session to register for.

    Returns:
        HttpResponse: Redirect to the session detail page (or to event
        registration if the attendee has not registered for the event yet).

    Raises:
        Http404: If no scheduled session of a published event with that pk
            exists.
    """
    # Only sessions of published events are open for public registration —
    # sessions of drafts / archived / cancelled events aren't bookable.
    session = get_object_or_404(
        Session, pk=pk, status="scheduled", event__status="published"
    )

    # Attendees must be registered for the parent event first.
    event_reg = _attendee_event_registration(request, session.event)
    if event_reg is None:
        messages.error(
            request, "Register for the event first, then book individual sessions."
        )
        return redirect("event_register", pk=session.event.pk)

    # If they already have a registration row, just re-activate or no-op.
    existing = SessionRegistration.objects.filter(
        session=session,
        event_registration=event_reg,
    ).first()
    if existing:
        if existing.registration_status == "cancelled":
            existing.registration_status = "confirmed"
            existing.save(update_fields=["registration_status"])
            messages.success(request, f"Re-registered for '{session.title}'.")
        else:
            messages.info(request, f"You're already registered for '{session.title}'.")
        return redirect("session_detail", pk=session.pk)

    # New registration: confirm if there is room, otherwise waitlist.
    confirmed_count = SessionRegistration.objects.filter(
        session=session,
        registration_status="confirmed",
    ).count()

    if confirmed_count >= session.max_capacity:
        SessionRegistration.objects.create(
            session=session,
            event_registration=event_reg,
            registration_status="waitlisted",
        )
        messages.warning(request, f"'{session.title}' is full. You're on the waitlist.")
    else:
        SessionRegistration.objects.create(
            session=session,
            event_registration=event_reg,
            registration_status="confirmed",
        )
        messages.success(request, f"You're registered for '{session.title}'.")

    return redirect("session_detail", pk=session.pk)


@require_POST
def session_unregister(request, pk):
    """Cancel the signed-in attendee's registration for a session.

    Args:
        request: The HttpRequest (POST only).
        pk: PK of the Session to unregister from.

    Returns:
        HttpResponse: Redirect to the session detail page.
    """
    session = get_object_or_404(Session, pk=pk)
    event_reg = _attendee_event_registration(request, session.event)
    session_reg = None
    if event_reg:
        session_reg = SessionRegistration.objects.filter(
            session=session,
            event_registration=event_reg,
            registration_status__in=["confirmed", "waitlisted"],
        ).first()

    if session_reg is None:
        messages.error(request, "You're not registered for this session.")
        return redirect("session_detail", pk=session.pk)

    session_reg.registration_status = "cancelled"
    session_reg.save(update_fields=["registration_status"])
    messages.success(request, f"You've unregistered from '{session.title}'.")
    return redirect("session_detail", pk=session.pk)


@login_required
def session_attendees(request, pk):
    """Organiser-only list of a session's registrations, with check-in buttons.

    Args:
        request: The HttpRequest.
        pk: PK of the Session whose attendees to list.

    Returns:
        HttpResponse: The rendered session_attendees.html, or a redirect to
        the events list if the user is not an organiser.

    Raises:
        Http404: If the session does not belong to the organiser's tenant.
    """
    tenant = _user_tenant(request)
    if tenant is None:
        messages.error(request, "Organiser access required.")
        return redirect("events")

    session = get_object_or_404(Session, pk=pk, event__tenant=tenant)
    registrations = (
        SessionRegistration.objects.filter(session=session)
        .select_related("event_registration__attendee")
        .order_by("registration_status", "registered_at")
    )

    # Summary counts drive the Google Charts doughnut on the page.
    confirmed_count = sum(
        1 for r in registrations if r.registration_status == "confirmed"
    )
    checked_in_count = sum(1 for r in registrations if r.checked_in_at is not None)

    return render(
        request,
        "session_attendees.html",
        {
            "session": session,
            "registrations": registrations,
            "confirmed_count": confirmed_count,
            "checked_in_count": checked_in_count,
            "spots_left": max(session.max_capacity - confirmed_count, 0),
        },
    )


@login_required
@require_POST
def session_check_in(request, pk, reg_pk):
    """Toggle the check-in timestamp on a SessionRegistration. Organiser-only.

    Args:
        request: The HttpRequest (POST only).
        pk: PK of the Session.
        reg_pk: PK of the SessionRegistration to check in / un-check.

    Returns:
        HttpResponse: Redirect back to the session attendees page.

    Raises:
        Http404: If the session does not belong to the organiser's tenant, or
        the registration does not belong to the session.
    """
    tenant = _user_tenant(request)
    if tenant is None:
        return redirect("events")

    session = get_object_or_404(Session, pk=pk, event__tenant=tenant)
    reg = get_object_or_404(SessionRegistration, pk=reg_pk, session=session)

    # Toggle: stamp the current time, or clear it if already checked in.
    if reg.checked_in_at:
        reg.checked_in_at = None
        action = "Un-checked"
    else:
        reg.checked_in_at = timezone.now()
        action = "Checked in"
    reg.save(update_fields=["checked_in_at"])

    name = (
        f"{reg.event_registration.attendee.first_name} "
        f"{reg.event_registration.attendee.last_name}"
    )
    messages.success(request, f"{action} {name}.")
    return redirect("session_attendees", pk=session.pk)


# --- Speakers (HTMX, organiser-only) ----------------------------------------


@login_required
@require_POST
def speaker_create(request, session_pk):
    """Add a speaker to a session (HTMX). Returns the refreshed speaker list.

    Args:
        request: The HttpRequest; POST data is a SessionSpeakerForm.
        session_pk: PK of the Session to attach the speaker to.

    Returns:
        HttpResponse: The rendered partials/_speaker_list.html fragment for
        HTMX to swap in.

    Raises:
        Http404: If the session does not belong to the organiser's tenant.
    """
    tenant = _user_tenant(request)
    session = get_object_or_404(Session, pk=session_pk, event__tenant=tenant)

    form = SessionSpeakerForm(request.POST)
    if form.is_valid():
        speaker = form.save(commit=False)
        speaker.session = session
        speaker.save()

    # Always return the current list so HTMX can swap it in, valid or not.
    return render(request, "partials/_speaker_list.html", {"session": session})


@login_required
@require_POST
def speaker_delete(request, pk):
    """Remove a speaker from a session (HTMX). Returns the refreshed list.

    Args:
        request: The HttpRequest (POST only).
        pk: PK of the SessionSpeaker to delete.

    Returns:
        HttpResponse: The rendered partials/_speaker_list.html fragment for
        HTMX to swap in.

    Raises:
        Http404: If the speaker's session does not belong to the organiser's
        tenant.
    """
    tenant = _user_tenant(request)
    speaker = get_object_or_404(SessionSpeaker, pk=pk, session__event__tenant=tenant)
    session = speaker.session
    speaker.delete()

    return render(request, "partials/_speaker_list.html", {"session": session})


# --- Calendar JSON feeds ----------------------------------------------------


def event_calendar_json(request):
    """Return published events as a JSON feed for FullCalendar.

    Public endpoint — no @login_required. Only published events are exposed.

    Args:
        request: The HttpRequest; optional query params:
            ``tenant``: slug to scope the feed to a single tenant.
            ``start`` / ``end``: ISO date bounds FullCalendar sends when
            navigating months, used to keep the payload small.

    Returns:
        JsonResponse: A list of FullCalendar event objects (safe=False).
    """
    qs = Event.objects.filter(status="published").select_related("tenant")

    # Optional per-tenant scoping.
    tenant_slug = request.GET.get("tenant")
    if tenant_slug:
        qs = qs.filter(tenant__slug=tenant_slug)

    # FullCalendar passes ?start=&end= when navigating months — honour them.
    # Bad values would raise ValueError from the ORM and 500 the endpoint;
    # parse_datetime returns None on garbage input, so we just skip the filter.
    start = parse_datetime(request.GET.get("start") or "")
    end = parse_datetime(request.GET.get("end") or "")
    if start:
        qs = qs.filter(end_time__gte=start)
    if end:
        qs = qs.filter(start_time__lte=end)

    events_payload = [
        {
            "id": event.pk,
            "title": event.title,
            "start": event.start_time.isoformat(),
            "end": event.end_time.isoformat(),
            "url": reverse("event_detail", args=[event.pk]),
            "extendedProps": {
                "tenant": event.tenant.name,
                "city": event.city or "",
                "cost": str(event.cost) if event.cost else "Free",
            },
        }
        for event in qs
    ]

    return JsonResponse(events_payload, safe=False)


def attendee_calendar(request):
    """Render the visitor's personal calendar of confirmed registrations.

    Registrations are gathered across every tenant (a user can have several
    per-tenant Attendee rows), matched by email.

    Args:
        request: The HttpRequest.

    Returns:
        HttpResponse: The rendered attendee_calendar.html, or a redirect to
        the events list if the visitor's email can't be resolved.
    """
    email = _calendar_email(request)
    if not email:
        messages.error(request, "Please sign in to view your calendar.")
        return redirect("events")

    # Most-recently-updated attendee row, just for display (name, etc.).
    attendee = (
        Attendee.objects.filter(email__iexact=email).order_by("-updated_at").first()
    )
    # Confirmed registrations across all tenants, matched by email.
    registrations = (
        EventRegistration.objects.filter(
            attendee__email__iexact=email,
            registration_status="confirmed",
        )
        .select_related("event")
        .order_by("event__start_time")
    )

    return render(
        request,
        "attendee_calendar.html",
        {
            "attendee": attendee,
            "registrations": registrations,
        },
    )


def attendee_calendar_json(request):
    """Return the visitor's confirmed registrations as a FullCalendar JSON feed.

    Args:
        request: The HttpRequest.

    Returns:
        JsonResponse: A list of FullCalendar event objects across all tenants
        (safe=False); an empty list if the visitor's email can't be resolved.
    """
    email = _calendar_email(request)
    if not email:
        return JsonResponse([], safe=False)

    registrations = EventRegistration.objects.filter(
        attendee__email__iexact=email,
        registration_status="confirmed",
    ).select_related("event", "event__tenant")

    payload = [
        {
            "id": reg.event.pk,
            "title": reg.event.title,
            "start": reg.event.start_time.isoformat(),
            "end": reg.event.end_time.isoformat(),
            "url": reverse("event_detail", args=[reg.event.pk]),
            "extendedProps": {
                "tenant": reg.event.tenant.name,
                "city": reg.event.city or "",
                "status": reg.get_registration_status_display(),
            },
            "color": "#198754",  # green — marks the visitor's own bookings
        }
        for reg in registrations
    ]
    return JsonResponse(payload, safe=False)


# --- AI: description polishing ---------------------------------


@login_required
@require_POST
def polish_description_view(request):
    """HTMX endpoint: take a rough event description, return a polished fragment.

    Args:
        request: The HttpRequest (POST only); reads ``description`` and
        optional ``title`` from POST data.

    Returns:
        HttpResponse: The rendered partials/_polished_description.html — with
        either a ``polished`` result or an ``error`` message.
    """
    rough = (request.POST.get("description") or "").strip()
    title = (request.POST.get("title") or "").strip()

    # Guard rails before spending an API call.
    if len(rough) < 10:
        return render(
            request,
            "partials/_polished_description.html",
            {"error": "Write at least a sentence or two before polishing."},
        )
    if len(rough) > 2000:
        return render(
            request,
            "partials/_polished_description.html",
            {"error": "Trim your draft to under 2000 characters first."},
        )

    # Any LLM/network failure is surfaced inline rather than 500-ing the page.
    # The raw exception text can include upstream URLs, request IDs and other
    # implementation detail we don't want to leak to end users, so it goes to
    # the logs and the user gets a generic message.
    try:
        polished = ai_polish_description(rough, event_title=title)
    except Exception:
        logger.exception("AI polish failed")
        return render(
            request,
            "partials/_polished_description.html",
            {"error": "The AI service is temporarily unavailable. Please try again in a moment."},
        )

    return render(
        request, "partials/_polished_description.html", {"polished": polished}
    )


# --- Signup & authentication ------------------------------------------------


def signup_choose(request):
    """Render the signup chooser page (attendee or organiser).

    Args:
        request: The HttpRequest.

    Returns:
        HttpResponse: The rendered signup_choose.html.
    """
    return render(request, "signup_choose.html")


def signup_form_fragment(request, role):
    """HTMX endpoint: return just the signup form HTML for the chosen role.

    Args:
        request: The HttpRequest.
        role: ``"organiser"`` for the organiser form; anything else returns
        the attendee form.

    Returns:
        HttpResponse: The rendered partial signup form for HTMX to swap in.
    """
    if role == "organiser":
        return render(
            request,
            "partials/_signup_form_organiser.html",
            {"form": OrganiserSignupForm()},
        )
    return render(request, "partials/_signup_form_attendee.html")


def organiser_signup(request):
    """Custom signup: create auth.User + Tenant + UserProfile together.

    Args:
        request: The HttpRequest; POST data is an OrganiserSignupForm.

    Returns:
        HttpResponse: Redirect to the events list on success (the new user is
        logged in), otherwise the re-rendered organiser_signup.html form.
    """
    if request.method == "POST":
        form = OrganiserSignupForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].lower()
            slug = form.cleaned_data["workspace_slug"].lower()

            # Uniqueness checks that span models — done here, not in the form.
            if User.objects.filter(email__iexact=email).exists():
                form.add_error(
                    "email",
                    "An account with this email already exists. Sign in instead.",
                )
            if Tenant.objects.filter(slug=slug).exists():
                form.add_error("workspace_slug", "That workspace URL is already taken.")

            if not form.errors:
                # Create the three linked rows in one transaction: if any of
                # User / Tenant / UserProfile fails (concurrent signup with the
                # same email or slug slipping past the existence checks above,
                # or a DB constraint tripping) the whole thing rolls back, so
                # we never leave an orphaned User or Tenant behind.
                try:
                    with transaction.atomic():
                        user = User.objects.create_user(
                            username=email,
                            email=email,
                            password=form.cleaned_data["password"],
                            first_name=form.cleaned_data["first_name"],
                            last_name=form.cleaned_data["last_name"],
                        )
                        tenant = Tenant.objects.create(
                            name=form.cleaned_data["workspace_name"],
                            slug=slug,
                            contact_email=email,
                            subscription_plan="free",
                            status="active",
                        )
                        UserProfile.objects.create(user=user, tenant=tenant)
                    # Explicit backend — allauth installs multiple auth backends.
                    login(
                        request, user,
                        backend="django.contrib.auth.backends.ModelBackend",
                    )
                    messages.success(
                        request, f"Workspace '{tenant.name}' created. Welcome!"
                    )
                    return redirect("events")
                except IntegrityError:
                    # Race between the existence checks and the writes — a
                    # second concurrent signup got there first.
                    form.add_error(
                        None,
                        "That email or workspace URL was just taken. Try again.",
                    )
    else:
        form = OrganiserSignupForm()

    return render(request, "organiser_signup.html", {"form": form})
