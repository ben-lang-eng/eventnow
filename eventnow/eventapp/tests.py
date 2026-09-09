# eventnow/eventapp/tests.py
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db.utils import OperationalError
from django.test import TestCase
from django.utils import timezone

from eventapp.models import (
    Attendee,
    Event,
    EventRegistration,
    Session,
    Tenant,
    UserProfile,
)


def make_tenant_and_organiser(slug="t1", email="org@example.com"):
    tenant = Tenant.objects.create(
        name=slug, slug=slug, contact_email=email,
    )
    user = User.objects.create_user(username=email, email=email, password="x")
    profile = UserProfile.objects.create(user=user, tenant=tenant)
    return tenant, user, profile


def make_event(tenant, organiser, **overrides):
    now = timezone.now()
    defaults = {
        "tenant": tenant, "organiser": organiser,
        "title": "Test Event", "status": "published",
        "start_time": now + timedelta(days=7),
        "end_time": now + timedelta(days=7, hours=2),
        "max_attendees": 100,
    }
    return Event.objects.create(**{**defaults, **overrides})


class DraftPrivacyTests(TestCase):
    """P0.3 — non-published events are private to the owning tenant."""

    def setUp(self):
        self.tenant, self.user, self.profile = make_tenant_and_organiser()
        _, self.other_user, _ = make_tenant_and_organiser(
            slug="t2", email="other@example.com")
        self.draft = make_event(self.tenant, self.profile,
                                status="draft", title="Draft")

    def test_public_gets_404_on_draft(self):
        response = self.client.get(f"/event/{self.draft.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_other_tenant_gets_404_on_draft(self):
        self.client.force_login(self.other_user)
        response = self.client.get(f"/event/{self.draft.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_owner_sees_draft(self):
        self.client.force_login(self.user)
        response = self.client.get(f"/event/{self.draft.pk}/")
        self.assertEqual(response.status_code, 200)


class SignupGateTests(TestCase):
    """P0.2 — allauth password signup is closed."""

    def test_allauth_signup_page_is_closed(self):
        response = self.client.get("/accounts/signup/")
        # Custom template renders instead of allauth's default; the page
        # points visitors at Google OAuth and the organiser signup flow.
        self.assertContains(response, "Password signup is closed", status_code=200)
        self.assertContains(response, "Continue with Google")
        self.assertNotContains(response, "password1")   # no password fields


class MapsUrlEncodingTests(TestCase):
    """P1 #4 — user-supplied address is URL-encoded in the Maps iframe."""

    def test_ampersand_in_address_is_encoded(self):
        tenant, _, profile = make_tenant_and_organiser()
        event = make_event(
            tenant, profile, address="1 R&D Building", city="Foo")
        response = self.client.get(f"/event/{event.pk}/")
        # unencoded would fail here
        self.assertNotContains(response, "q=1 R&D Building")
        self.assertContains(response, "R%26D")                  # & encoded


class OrganiserSignupTests(TestCase):
    """P1 #5 — organiser signup handles duplicates cleanly, no orphans."""

    def test_duplicate_email_shows_form_error(self):
        User.objects.create_user(
            username="taken@example.com", email="taken@example.com", password="x")
        response = self.client.post("/signup/organiser/", {
            "first_name": "Jane", "last_name": "Doe",
            "email": "taken@example.com", "password": "correcthorsebattery",
            "workspace_name": "New Space", "workspace_slug": "new-space",
        })
        self.assertContains(response, "already exists")
        self.assertEqual(User.objects.filter(
            email="taken@example.com").count(), 1)
        self.assertFalse(Tenant.objects.filter(
            slug="new-space").exists())  # no orphan

    def test_happy_path_creates_all_three_rows(self):
        response = self.client.post("/signup/organiser/", {
            "first_name": "Jane", "last_name": "Doe",
            "email": "new@example.com", "password": "correcthorsebattery",
            "workspace_name": "New Space", "workspace_slug": "new-space",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email="new@example.com").exists())
        self.assertTrue(Tenant.objects.filter(slug="new-space").exists())
        self.assertTrue(UserProfile.objects.filter(
            user__email="new@example.com").exists())

    def test_duplicate_slug_shows_form_error(self):
        Tenant.objects.create(
            name="Existing", slug="taken-slug", contact_email="e@example.com",
        )
        response = self.client.post("/signup/organiser/", {
            "first_name": "Jane", "last_name": "Doe",
            "email": "unused@example.com", "password": "correcthorsebattery",
            "workspace_name": "New Space", "workspace_slug": "taken-slug",
        })
        self.assertContains(response, "already taken")
        # No orphan User row created for the failed attempt.
        self.assertFalse(User.objects.filter(email="unused@example.com").exists())


class AiPolishExceptionTests(TestCase):
    """P1 #6 — API errors don't leak upstream exception text."""

    def test_generic_error_message_on_ai_failure(self):
        _, user, _ = make_tenant_and_organiser()
        self.client.force_login(user)

        with patch(
            "eventapp.views.ai_polish_description",
            side_effect=Exception(
                "secret upstream URL https://internal.example.com/xyz"),
        ):
            response = self.client.post("/ai/polish-description/", {
                "description": "A long-enough rough draft goes here.",
                "title": "Test",
            })

        self.assertNotContains(response, "internal.example.com")
        self.assertNotContains(response, "secret upstream URL")
        self.assertContains(response, "temporarily unavailable")


class DraftListingTests(TestCase):
    """Follow-up UI fix — organisers see their own drafts on /events/."""

    def setUp(self):
        self.tenant, self.user, self.profile = make_tenant_and_organiser()
        self.published = make_event(
            self.tenant, self.profile, title="Public one", status="published")
        self.draft = make_event(self.tenant, self.profile,
                                title="My draft", status="draft")

    def test_public_sees_only_published(self):
        response = self.client.get("/events/")
        self.assertContains(response, "Public one")
        self.assertNotContains(response, "My draft")

    def test_owner_sees_own_drafts_with_badge(self):
        self.client.force_login(self.user)
        response = self.client.get("/events/")
        self.assertContains(response, "My draft")
        self.assertContains(response, "Draft")   # the badge


class PasswordLoginTests(TestCase):
    """Existing password accounts can still sign in after signup was closed."""

    def test_password_login_works_for_existing_user(self):
        User.objects.create_user(
            username="existing@example.com",
            email="existing@example.com",
            password="strongpass123",
        )
        # allauth's login endpoint takes "login" (email) and "password".
        response = self.client.post("/accounts/login/", {
            "login": "existing@example.com",
            "password": "strongpass123",
        })
        # 302 = redirect on successful auth; 200 would mean the form re-rendered
        # with an error.
        self.assertEqual(response.status_code, 302)


class SessionPrivacyTests(TestCase):
    """P0.3 — sessions of non-published events are private too."""

    def setUp(self):
        self.tenant, self.user, self.profile = make_tenant_and_organiser()
        self.draft_event = make_event(
            self.tenant, self.profile, status="draft", title="Hidden Event",
        )
        self.session = Session.objects.create(
            event=self.draft_event,
            title="Hidden Session",
            start_time=self.draft_event.start_time,
            end_time=self.draft_event.end_time,
            max_capacity=50,
            status="scheduled",
        )

    def test_session_of_draft_event_is_404_for_public(self):
        response = self.client.get(f"/session/{self.session.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_session_of_draft_event_visible_to_owner(self):
        self.client.force_login(self.user)
        response = self.client.get(f"/session/{self.session.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_session_register_404s_for_session_of_draft_event(self):
        # Even a POST attempt to book a session under a draft event should
        # 404 — the get_object_or_404 filter scopes to event__status='published'.
        response = self.client.post(f"/session/{self.session.pk}/register/")
        self.assertEqual(response.status_code, 404)


class TenantIsolationTests(TestCase):
    """An organiser cannot touch another tenant's events."""

    def setUp(self):
        self.t1, self.u1, self.p1 = make_tenant_and_organiser(
            slug="t1", email="org1@example.com",
        )
        self.t2, self.u2, _ = make_tenant_and_organiser(
            slug="t2", email="org2@example.com",
        )
        self.event = make_event(self.t1, self.p1, title="T1's Event")

    def test_cannot_edit_other_tenants_event(self):
        self.client.force_login(self.u2)
        response = self.client.get(f"/event/{self.event.pk}/edit/")
        self.assertEqual(response.status_code, 404)

    def test_cannot_archive_other_tenants_event(self):
        self.client.force_login(self.u2)
        response = self.client.post(f"/event/{self.event.pk}/archive/")
        self.assertEqual(response.status_code, 404)
        # Event was not archived by the failed attempt.
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "published")


class AttendeeFlowTests(TestCase):
    """Register for an event, see it on My Calendar, unregister."""

    def setUp(self):
        self.tenant, _, self.profile = make_tenant_and_organiser()
        self.event = make_event(
            self.tenant, self.profile, title="Cheese Rolling",
        )
        self.attendee_user = User.objects.create_user(
            username="alice@example.com",
            email="alice@example.com",
            password="strongpass123",
            first_name="Alice", last_name="Smith",
        )

    def test_register_for_event_creates_registration_row(self):
        self.client.force_login(self.attendee_user)
        response = self.client.post(f"/event/{self.event.pk}/register/", {
            "first_name": "Alice", "last_name": "Smith",
            "email": "alice@example.com", "phone": "",
        })
        self.assertEqual(response.status_code, 302)
        attendee = Attendee.objects.get(
            email="alice@example.com", tenant=self.tenant,
        )
        self.assertTrue(EventRegistration.objects.filter(
            event=self.event, attendee=attendee,
            registration_status="confirmed",
        ).exists())

    def test_attendee_calendar_shows_confirmed_registrations(self):
        attendee = Attendee.objects.create(
            tenant=self.tenant, first_name="Alice", last_name="Smith",
            email="alice@example.com",
        )
        EventRegistration.objects.create(
            event=self.event, attendee=attendee,
            registration_status="confirmed",
        )
        self.client.force_login(self.attendee_user)
        response = self.client.get("/my-calendar/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cheese Rolling")

    def test_unregister_cancels_registration(self):
        attendee = Attendee.objects.create(
            tenant=self.tenant, first_name="Alice", last_name="Smith",
            email="alice@example.com",
        )
        reg = EventRegistration.objects.create(
            event=self.event, attendee=attendee,
            registration_status="confirmed",
        )
        # Seed the attendee session keys the way signals.link_attendee_session
        # would after login — event_unregister reads attendee_id from session.
        session = self.client.session
        session["attendee_id"] = attendee.pk
        session["attendee_tenant_id"] = self.tenant.pk
        session.save()

        self.client.force_login(self.attendee_user)
        # After force_login the signal fires and overwrites the session; re-set.
        session = self.client.session
        session["attendee_id"] = attendee.pk
        session.save()

        response = self.client.post(f"/event/{self.event.pk}/unregister/")
        self.assertEqual(response.status_code, 302)
        reg.refresh_from_db()
        self.assertEqual(reg.registration_status, "cancelled")


class AdminSmokeTests(TestCase):
    """Django admin serves for a superuser."""

    def test_django_admin_loads_for_superuser(self):
        admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="adminpass123",
        )
        self.client.force_login(admin)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Site administration")


class HealthEndpointTests(TestCase):
    """The probes Grafana Synthetic Monitoring polls.

    Both must work unauthenticated — the monitor has no session — and
    /healthz/ must not touch the database, or the Neon compute budget goes
    to health checks instead of users.
    """

    def test_healthz_is_public_and_ok(self):
        with patch.dict("os.environ", {"LLM_API_KEY": "test-key"}):
            response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["checks"]["llm_config"], "ok")

    def test_healthz_reports_degraded_without_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            response = self.client.get("/healthz/")
        # Degraded, not down: the site serves fine with the AI button broken,
        # so this must not page anyone via the HTTP status code.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "degraded")

    def test_healthz_runs_no_queries(self):
        with patch.dict("os.environ", {"LLM_API_KEY": "test-key"}):
            with self.assertNumQueries(0):
                self.client.get("/healthz/")

    def test_readyz_checks_the_database(self):
        with patch.dict("os.environ", {"LLM_API_KEY": "test-key"}):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"]["db"], "ok")

    def test_readyz_returns_503_when_the_database_is_unreachable(self):
        with patch("eventapp.health.connection") as fake_connection:
            fake_connection.cursor.side_effect = OperationalError("down")
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["checks"]["db"], "error: OperationalError")
        # The driver's message can carry the database host and credentials;
        # only the exception class name may reach a public endpoint.
        self.assertNotIn("down", response.content.decode())

    def test_probes_reject_post(self):
        # 405 from @require_GET under the test client, which is CSRF-exempt;
        # 403 from CsrfViewMiddleware in front of a real client, which gets
        # there first. Either way the probes are read-only.
        for url in ("/healthz/", "/readyz/"):
            self.assertIn(self.client.post(url).status_code, (403, 405))


class StatusDashboardTests(TestCase):
    """/status/ — the public link to the Grafana dashboard."""

    def test_redirects_to_configured_dashboard(self):
        url = "https://example.grafana.net/public-dashboards/abc123"
        with patch.dict("os.environ", {"STATUS_DASHBOARD_URL": url}):
            response = self.client.get("/status/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], url)

    def test_404_when_unconfigured(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(self.client.get("/status/").status_code, 404)
