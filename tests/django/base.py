from django.test import TestCase as _TestCase, RequestFactory
from django.conf import settings
from django.utils.module_loading import import_module


class RequestClient(RequestFactory):
    @property
    def session(self):
        engine = import_module(settings.SESSION_ENGINE)
        cookie = self.cookies.get(settings.SESSION_COOKIE_NAME)
        if cookie:
            return engine.SessionStore(cookie.value)

        session = engine.SessionStore()
        session.save()
        self.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        return session


class TestCase(_TestCase):
    @classmethod
    def setUpClass(cls):
        super(TestCase, cls).setUpClass()
        from . import settings as settings_module
        if not settings.configured:
            settings.configure(settings_module.__dict__)

    def setUp(self):
        self.factory = RequestClient()
