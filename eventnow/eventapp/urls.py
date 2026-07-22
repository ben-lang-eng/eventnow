from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    # Events
    path("events/", views.event_list, name="events"),
    path("events-feed/", views.event_calendar_json, name="event_calendar_json"),
    path("event/new/", views.event_create, name="event_create"),
    path("ai/polish-description/", views.polish_description_view, name="polish_description"),
    path("event/<int:pk>/", views.event_detail, name="event_detail"),
    path("event/<int:pk>/edit/", views.event_edit, name="event_edit"),
    path("event/<int:pk>/archive/", views.event_archive, name="event_archive"),
    path("event/<int:pk>/register/", views.event_register, name="event_register"),
    path("event/<int:pk>/unregister/", views.event_unregister, name="event_unregister"),
    # Calendar
    path("my-calendar/", views.attendee_calendar, name="attendee_calendar"),
    path("my-calendar/feed/", views.attendee_calendar_json, name="attendee_calendar_json"),
    # Signup
    path("signup/form/<str:role>/", views.signup_form_fragment, name="signup_form_fragment"),
    path("signup/", views.signup_choose, name="signup_choose"),
    path("signup/organiser/", views.organiser_signup, name="organiser_signup"),
    # Sessions
    path("session/<int:pk>/register/", views.session_register, name="session_register"),
    path("session/<int:pk>/attendees/", views.session_attendees, name="session_attendees"),
    path("session/<int:pk>/registration/<int:reg_pk>/check-in/", views.session_check_in, name="session_check_in",),
    path("event/<int:event_pk>/session/new/", views.session_create, name="session_create"),
    path("session/<int:pk>/", views.session_detail, name="session_detail"),
    path("session/<int:pk>/edit/", views.session_edit, name="session_edit"),
    path("session/<int:session_pk>/speaker/new/", views.speaker_create, name="speaker_create"),
    path("speaker/<int:pk>/delete/", views.speaker_delete, name="speaker_delete"),
    path("session/<int:pk>/archive/", views.session_archive, name="session_archive"),
    path("session/<int:pk>/unregister/", views.session_unregister, name="session_unregister"),
    # Calendar
    path("calendar/", views.calendar, name="calendar"),
    # Static legal pages
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
]
