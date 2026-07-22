"""Django admin configuration for EventNow.

Registers every model with sensible list displays, filters and search.
TenantAdmin enforces soft-delete (archive) instead of hard deletion;
AuditLog is read-only.
"""

from django.contrib import admin

from . import models


@admin.register(models.Tenant)
class TenantAdmin(admin.ModelAdmin):
    """Admin for Tenant — soft-delete only, never hard-deleted."""

    list_display = ("name", "slug", "subscription_plan", "status", "created_at")
    list_filter = ("subscription_plan", "status")
    search_fields = ("name", "slug", "contact_email")
    prepopulated_fields = {"slug": ("name",)}
    list_per_page = 25
    actions = ["archive_selected", "reactivate_selected"]

    def archive_selected(self, request, queryset):
        """Bulk action: soft-delete tenants by setting status='archived'.

        Args:
            request: The admin HttpRequest.
            queryset: The selected Tenant rows.
        """
        updated = queryset.update(status="archived")
        self.message_user(request, f"{updated} tenant(s) archived.")

    archive_selected.short_description = "Archive selected tenants (soft delete)"

    def reactivate_selected(self, request, queryset):
        """Bulk action: restore archived tenants to status='active'.

        Args:
            request: The admin HttpRequest.
            queryset: The selected Tenant rows.
        """
        updated = queryset.update(status="active")
        self.message_user(request, f"{updated} tenant(s) reactivated.")

    reactivate_selected.short_description = "Reactivate selected tenants"

    def has_delete_permission(self, request, obj=None):
        """Disable hard deletion — tenants are only ever soft-deleted.

        Args:
            request: The admin HttpRequest.
            obj: The Tenant being inspected, or None for the list view.

        Returns:
            bool: Always False.
        """
        return False


@admin.register(models.UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Admin for UserProfile — links an auth.User to a Tenant."""

    list_display = ("user", "tenant", "phone")
    list_filter = ("tenant",)
    search_fields = (
        "user__username", "user__email",
        "user__first_name", "user__last_name",
    )


@admin.register(models.Attendee)
class AttendeeAdmin(admin.ModelAdmin):
    """Admin for Attendee — per-tenant attendee records."""

    list_display = ("first_name", "last_name", "email", "tenant", "is_active")
    list_filter = ("tenant", "is_active")
    search_fields = ("first_name", "last_name", "email")


@admin.register(models.Event)
class EventAdmin(admin.ModelAdmin):
    """Admin for Event, with a date drill-down on start_time."""

    list_display = ("title", "tenant", "organiser", "start_time", "status", "max_attendees")
    list_filter = ("tenant", "status", "start_time")
    search_fields = ("title", "description", "city")
    date_hierarchy = "start_time"
    prepopulated_fields = {"event_website_slug": ("title",)}


@admin.register(models.Session)
class SessionAdmin(admin.ModelAdmin):
    """Admin for Session — sessions within an event."""

    list_display = ("title", "event", "start_time", "max_capacity", "status")
    list_filter = ("event__tenant", "status", "start_time")
    search_fields = ("title", "description", "event__title")


@admin.register(models.SessionSpeaker)
class SessionSpeakerAdmin(admin.ModelAdmin):
    """Admin for SessionSpeaker — speakers attached to a session."""

    list_display = ("display_name", "session", "user")
    search_fields = ("display_name", "session__title")


@admin.register(models.EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    """Admin for EventRegistration — an attendee's place at an event."""

    list_display = ("attendee", "event", "registration_status", "registered_at", "checked_in_at")
    list_filter = ("registration_status", "event__tenant")
    search_fields = ("attendee__email", "event__title")


@admin.register(models.SessionRegistration)
class SessionRegistrationAdmin(admin.ModelAdmin):
    """Admin for SessionRegistration — an attendee's place at a session."""

    list_display = ("event_registration", "session", "registration_status", "registered_at", "checked_in_at")
    list_filter = ("registration_status",)


