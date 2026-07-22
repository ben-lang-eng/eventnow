"""Seed the local SQLite database with realistic EventNow demo data.

Run from this folder (where manage.py is):

    python seed_data.py

Passwords are read from env vars where set, otherwise a random one is
generated per account and printed at the end of the run — so this script
is safe to keep in a public repo (no secrets baked in).

Env vars (all optional):
    SEED_SUPERUSER_PASSWORD
    SEED_ORGANISER_PASSWORD          (shared by all three demo organisers)
    SEED_ATTENDEE_PASSWORD

What you get:
- Superuser: s4844298 (for /admin/)
- 3 organisers + tenants:
    uqdogpatters@proton.me   (UQ Dog Patters)
    uqgoatpatters@proton.me  (UQ Goat Patters)
    uqrandomevents@proton.me (UQ Random Events)
- 1 attendee user:
    alanattendee@proton.me
- 9 published events spread across the next ~9 weeks (enough to demo pagination)
- 2 sessions per event (most events; a couple have 0–1)
- Alan registered for 3 events with one session each

Idempotent: re-running won't duplicate rows. Existing accounts keep their
current passwords (create_user is skipped for them). To start completely
fresh, delete db.sqlite3 and re-run `python manage.py migrate` first.
"""
import os
import secrets
from datetime import timedelta
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eventnow.settings")
import django

django.setup()

from django.contrib.auth.models import User
from django.utils import timezone

from eventapp.models import (
    Attendee,
    Event,
    EventRegistration,
    Session,
    SessionRegistration,
    Tenant,
    UserProfile,
)


# Passwords used by the seed. Env vars win; otherwise a fresh random 16-char
# password is generated once per run and reported at the end. Keeping these
# out of source means the file is safe to publish.
SUPERUSER_PASSWORD = os.environ.get(
    "SEED_SUPERUSER_PASSWORD") or secrets.token_urlsafe(16)
ORGANISER_PASSWORD = os.environ.get(
    "SEED_ORGANISER_PASSWORD") or secrets.token_urlsafe(16)
ATTENDEE_PASSWORD = os.environ.get(
    "SEED_ATTENDEE_PASSWORD") or secrets.token_urlsafe(16)


# --- Helpers ---------------------------------------------------------------


def ensure_superuser():
    """Create the admin superuser if it doesn't already exist."""
    if User.objects.filter(username="s4844298").exists():
        print("  Superuser s4844298 already exists.")
        return
    user = User.objects.create_superuser(
        username="s4844298",
        email="s4844298@uq.edu.au",
        password=SUPERUSER_PASSWORD,
    )
    print(f"  Created superuser: {user.username}")


def ensure_organiser(
    *, email, password, first_name, last_name, workspace_name, workspace_slug
):
    """Create User + Tenant + UserProfile, idempotent on email."""
    user, created = User.objects.get_or_create(
        username=email,
        defaults={
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        },
    )
    if created:
        user.set_password(password)
        user.save()
        print(f"  Created organiser user: {email}")

    tenant, t_created = Tenant.objects.get_or_create(
        slug=workspace_slug,
        defaults={
            "name": workspace_name,
            "contact_email": email,
            "subscription_plan": "free",
            "status": "active",
        },
    )
    if t_created:
        print(f"  Created tenant: {workspace_name}")

    profile, _ = UserProfile.objects.get_or_create(
        user=user, defaults={"tenant": tenant}
    )
    return profile, tenant


def ensure_attendee_user(email, password, first_name, last_name):
    """Create an attendee auth.User (no UserProfile)."""
    user, created = User.objects.get_or_create(
        username=email,
        defaults={
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        },
    )
    if created:
        user.set_password(password)
        user.save()
        print(f"  Created attendee user: {email}")
    return user


def make_event(
    tenant,
    organiser,
    *,
    title,
    description,
    days_from_now,
    duration_hours,
    address,
    city,
    max_attendees,
    cost=None,
    latitude=None,
    longitude=None,
):
    """Create one published event. Idempotent on (tenant, title)."""
    start = timezone.now() + timedelta(days=days_from_now)
    end = start + timedelta(hours=duration_hours)
    event, created = Event.objects.get_or_create(
        tenant=tenant,
        title=title,
        defaults={
            "organiser": organiser,
            "description": description,
            "start_time": start,
            "end_time": end,
            "address": address,
            "city": city,
            "latitude": latitude,
            "longitude": longitude,
            "max_attendees": max_attendees,
            "cost": cost,
            "status": "published",
        },
    )
    if created:
        print(f"  Event: {title}")
    return event


def make_session(
    event, *, title, description, hours_offset, duration_hours, room_name,
    max_capacity,
):
    """Create one scheduled session under an event. Idempotent on (event, title)."""
    start = event.start_time + timedelta(hours=hours_offset)
    end = start + timedelta(hours=duration_hours)
    session, created = Session.objects.get_or_create(
        event=event,
        title=title,
        defaults={
            "description": description,
            "start_time": start,
            "end_time": end,
            "room_name": room_name,
            "max_capacity": max_capacity,
            "status": "scheduled",
        },
    )
    if created:
        print(f"    Session: {title}")
    return session


def register_attendee(tenant, *, email, first_name, last_name, event, session=None):
    """Create Attendee + EventRegistration (+ optional SessionRegistration)."""
    attendee, _ = Attendee.objects.get_or_create(
        tenant=tenant,
        email=email,
        defaults={"first_name": first_name, "last_name": last_name, "phone": ""},
    )
    event_reg, _ = EventRegistration.objects.get_or_create(
        event=event,
        attendee=attendee,
        defaults={"registration_status": "confirmed"},
    )
    if session is not None:
        SessionRegistration.objects.get_or_create(
            session=session,
            event_registration=event_reg,
            defaults={"registration_status": "confirmed"},
        )


# --- Main ------------------------------------------------------------------


def main():
    print("Seeding EventNow local database...\n")

    print("Superuser:")
    ensure_superuser()

    print("\nOrganisers + tenants:")
    dogs_org, dogs_tenant = ensure_organiser(
        email="uqdogpatters@proton.me",
        password=ORGANISER_PASSWORD,
        first_name="Daisy",
        last_name="Dogpatter",
        workspace_name="UQ Dog Patters",
        workspace_slug="uq-dog-patters",
    )
    goats_org, goats_tenant = ensure_organiser(
        email="uqgoatpatters@proton.me",
        password=ORGANISER_PASSWORD,
        first_name="Gary",
        last_name="Goatpatter",
        workspace_name="UQ Goat Patters",
        workspace_slug="uq-goat-patters",
    )
    random_org, random_tenant = ensure_organiser(
        email="uqrandomevents@proton.me",
        password=ORGANISER_PASSWORD,
        first_name="Rita",
        last_name="Randomson",
        workspace_name="UQ Random Events",
        workspace_slug="uq-random-events",
    )

    print("\nAttendee user:")
    ensure_attendee_user(
        email="alanattendee@proton.me",
        password=ATTENDEE_PASSWORD,
        first_name="Alan",
        last_name="Attendee",
    )

    print("\nEvents:")
    dog_fest = make_event(
        dogs_tenant, dogs_org,
        title="UQ Dog Patting Festival",
        description=(
            "If you love dogs and enjoy a good pat, the UQ Dog Patting Festival "
            "is the event for you. This relaxed, friendly day brings together "
            "three popular breeds for plenty of canine cuddles and meet-and-greets."
        ),
        days_from_now=14, duration_hours=6,
        address="1 University Drive (Forgan Smith Top Lawn)",
        city="Saint Lucia", max_attendees=500,
        latitude=Decimal("-27.4975"), longitude=Decimal("153.0137"),
    )
    goat_fest = make_event(
        goats_tenant, goats_org,
        title="UQ Goat Patting Festival",
        description=(
            "If you love goats — especially patting and feeding them — the UQ "
            "Goat Patting Festival is made for you. Meet a lively lineup of "
            "Saanen, Toggenburg, Boer and LaMancha goats, and enjoy hands-on time "
            "with these much-loved animals."
        ),
        days_from_now=21, duration_hours=8,
        address="1 University Drive (Forgan Smith Top Lawn)",
        city="Saint Lucia", max_attendees=200,
        latitude=Decimal("-27.4975"), longitude=Decimal("153.0137"),
    )
    cow_fest = make_event(
        random_tenant, random_org,
        title="Tatura Cow Milking Festival",
        description=(
            "Do you like cows, milk, Tatura, or country Victoria? Then come along "
            "to the Tatura Cow Milking Festival! Enjoy a close-up look at three "
            "breeds of cow on display: Holstein Friesian, Jersey, and more."
        ),
        days_from_now=28, duration_hours=10,
        address="Tatura Park", city="Tatura",
        max_attendees=300,
    )
    brass = make_event(
        random_tenant, random_org,
        title="UQ Battle of the Brass Bands",
        description=(
            "Watch local brass bands compete in a thrilling all-day battle of the "
            "bands. Multiple categories, multiple bands, one trophy."
        ),
        days_from_now=10, duration_hours=5,
        address="UQ Centre", city="Saint Lucia",
        max_attendees=800, cost=Decimal("15.00"),
    )
    robotics = make_event(
        random_tenant, random_org,
        title="UQ Annual Robotics Fair",
        description=(
            "Showcasing student and faculty robotics projects from across UQ. "
            "Live demonstrations, interactive exhibits, and competitive showdowns "
            "in drone racing and underwater autonomy."
        ),
        days_from_now=35, duration_hours=8,
        address="UQ Engineering Building", city="Saint Lucia",
        max_attendees=400,
    )
    open_day = make_event(
        random_tenant, random_org,
        title="UQ Open Day",
        description=(
            "An annual showcase of UQ for prospective students and their families. "
            "Campus tours, faculty stalls, and student ambassadors ready to answer "
            "every question."
        ),
        days_from_now=42, duration_hours=8,
        address="UQ Saint Lucia Campus", city="Saint Lucia",
        max_attendees=2000,
    )
    sustainability = make_event(
        random_tenant, random_org,
        title="UQ Sustainability Expo",
        description=(
            "A celebration of sustainability innovations from research and industry. "
            "Speakers, workshops, and exhibits across renewables, circular economy, "
            "and climate adaptation."
        ),
        days_from_now=49, duration_hours=6,
        address="UQ Great Court", city="Saint Lucia",
        max_attendees=600,
    )
    cat_fest = make_event(
        dogs_tenant, dogs_org,
        title="UQ Cat Cuddling Festival",
        description=(
            "We don't only do dogs. Spend a relaxing afternoon with friendly cats "
            "of various breeds, ready for cuddles and play."
        ),
        days_from_now=56, duration_hours=4,
        address="UQ Pet Annex", city="Saint Lucia",
        max_attendees=150,
    )
    wool = make_event(
        goats_tenant, goats_org,
        title="Toowoomba Wool Festival",
        description=(
            "Celebrating wool from sheep, alpaca, and goat. Demonstrations of "
            "shearing, spinning, dyeing, and modern fibre design."
        ),
        days_from_now=63, duration_hours=7,
        address="Toowoomba Showgrounds", city="Toowoomba",
        max_attendees=400, cost=Decimal("10.00"),
    )

    print("\nSessions:")
    print("  UQ Dog Patting Festival:")
    lab_session = make_session(
        dog_fest, title="Labrador Patting Session",
        description="Meet a friendly group of Labradors and spend time getting to know them.",
        hours_offset=0, duration_hours=2, room_name="Forgan Smith Top Lawn",
        max_capacity=200,
    )
    make_session(
        dog_fest, title="Border Collie Patting Session",
        description="An enthusiastic, energetic breed — meet our resident Border Collies.",
        hours_offset=2, duration_hours=2, room_name="Forgan Smith Top Lawn",
        max_capacity=150,
    )

    print("  UQ Goat Patting Festival:")
    saanen = make_session(
        goat_fest, title="Saanen Goat Patting",
        description="The largest of the dairy goat breeds. Friendly, calm, and great with people.",
        hours_offset=0, duration_hours=2, room_name="Forgan Smith Upper Lawn",
        max_capacity=80,
    )
    make_session(
        goat_fest, title="Boer Goat Patting",
        description="South African meat breed, friendly and chunky.",
        hours_offset=3, duration_hours=2, room_name="Forgan Smith Upper Lawn",
        max_capacity=80,
    )

    print("  Tatura Cow Milking Festival:")
    holstein = make_session(
        cow_fest, title="Holstein Friesian Milking Session",
        description="The premier dairy breed. Watch them being milked, ask questions about dairy processing.",
        hours_offset=0, duration_hours=2, room_name="Tatura Park Dairy",
        max_capacity=100,
    )
    make_session(
        cow_fest, title="Jersey Cow Milking Session",
        description="Smaller dairy cow with rich, creamy milk. Hands-on demonstrations.",
        hours_offset=3, duration_hours=2, room_name="Tatura Park Dairy",
        max_capacity=100,
    )

    print("  UQ Battle of the Brass Bands:")
    make_session(
        brass, title="Junior Brass Battle",
        description="School-aged brass ensembles compete for the junior trophy.",
        hours_offset=0, duration_hours=2, room_name="UQ Centre Main Hall",
        max_capacity=400,
    )
    make_session(
        brass, title="Senior Brass Battle",
        description="Adult and university bands compete for the senior trophy.",
        hours_offset=3, duration_hours=2, room_name="UQ Centre Main Hall",
        max_capacity=400,
    )

    print("  UQ Annual Robotics Fair:")
    make_session(
        robotics, title="Underwater Robotics Showcase",
        description="Demonstrations of underwater autonomous vehicles built by students.",
        hours_offset=1, duration_hours=2, room_name="Engineering Building",
        max_capacity=200,
    )
    make_session(
        robotics, title="Drone Racing Final",
        description="The culmination of the fair: high-speed drone racing competition.",
        hours_offset=5, duration_hours=2, room_name="UQ Sports Hall",
        max_capacity=300,
    )

    print("  UQ Open Day:")
    make_session(
        open_day, title="Faculty of Engineering Tour",
        description="Tour the engineering facilities with current students.",
        hours_offset=2, duration_hours=1, room_name="Engineering Building",
        max_capacity=50,
    )
    make_session(
        open_day, title="Faculty of Science Tour",
        description="Tour the science facilities with current students.",
        hours_offset=3, duration_hours=1, room_name="Science Building",
        max_capacity=50,
    )

    print("\nSample registrations for Alan:")
    register_attendee(
        dogs_tenant, email="alanattendee@proton.me",
        first_name="Alan", last_name="Attendee",
        event=dog_fest, session=lab_session,
    )
    register_attendee(
        goats_tenant, email="alanattendee@proton.me",
        first_name="Alan", last_name="Attendee",
        event=goat_fest, session=saanen,
    )
    register_attendee(
        random_tenant, email="alanattendee@proton.me",
        first_name="Alan", last_name="Attendee",
        event=cow_fest, session=holstein,
    )

    print("\nDone.\n")
    print("Logins (write these down — random passwords are only printed here):")
    print("  Admin (browse to /admin/):")
    print(f"    s4844298 / {SUPERUSER_PASSWORD}")
    print("  Organisers (browse to /accounts/login/):")
    print(f"    uqdogpatters@proton.me   / {ORGANISER_PASSWORD}")
    print(f"    uqgoatpatters@proton.me  / {ORGANISER_PASSWORD}")
    print(f"    uqrandomevents@proton.me / {ORGANISER_PASSWORD}")
    print("  Attendee:")
    print(f"    alanattendee@proton.me / {ATTENDEE_PASSWORD}")


if __name__ == "__main__":
    main()
