from authlib.oauth2.rfc6749.grants import ClientCredentialsGrant
from .database import db
from .models import User, Client
from .oauth2_server import TestCase
from .oauth2_server import create_authorization_server


class ClientCredentialsTest(TestCase):
    def prepare_data(self, grant_type='client_credentials'):
        server = create_authorization_server(self.app)
        server.register_grant(ClientCredentialsGrant)
        self.server = server

        user = User(username='foo')
        db.add(user)
        db.commit()
        client = Client(
            user_id=user.id,
            client_id='credential-client',
            client_secret='credential-secret',
        )
        client.set_client_metadata({
            'scope': 'profile',
            'redirect_uris': ['http://localhost/authorized'],
            'grant_types': [grant_type]
        })
        db.add(client)
        db.commit()

    def test_invalid_client(self):
        self.prepare_data()
        rv = self.client.post('/oauth/token', data={
            'grant_type': 'client_credentials',
        })
        resp = rv.json()
        self.assertEqual(resp['error'], 'invalid_client')

        headers = self.create_basic_header(
            'credential-client', 'invalid-secret'
        )
        rv = self.client.post('/oauth/token', data={
            'grant_type': 'client_credentials',
        }, headers=headers)
        resp = rv.json()
        self.assertEqual(resp['error'], 'invalid_client')

    def test_invalid_grant_type(self):
        self.prepare_data(grant_type='invalid')
        headers = self.create_basic_header(
            'credential-client', 'credential-secret'
        )
        rv = self.client.post('/oauth/token', data={
            'grant_type': 'client_credentials',
        }, headers=headers)
        resp = rv.json()
        self.assertEqual(resp['error'], 'unauthorized_client')

    def test_invalid_scope(self):
        self.prepare_data()
        self.server.metadata = {'scopes_supported': ['profile']}
        headers = self.create_basic_header(
            'credential-client', 'credential-secret'
        )
        rv = self.client.post('/oauth/token', data={
            'grant_type': 'client_credentials',
            'scope': 'invalid',
        }, headers=headers)
        resp = rv.json()
        self.assertEqual(resp['error'], 'invalid_scope')

    def test_authorize_token(self):
        self.prepare_data()
        headers = self.create_basic_header(
            'credential-client', 'credential-secret'
        )
        rv = self.client.post('/oauth/token', data={
            'grant_type': 'client_credentials',
        }, headers=headers)
        resp = rv.json()
        self.assertIn('access_token', resp)

    def test_token_generator(self):
        m = 'tests.fastapi.test_oauth2.oauth2_server:token_generator'
        self.app.config.update({'OAUTH2_ACCESS_TOKEN_GENERATOR': m})

        self.prepare_data()
        headers = self.create_basic_header(
            'credential-client', 'credential-secret'
        )
        rv = self.client.post('/oauth/token', data={
            'grant_type': 'client_credentials',
        }, headers=headers)
        resp = rv.json()
        self.assertIn('access_token', resp)
        self.assertIn('c-client_credentials.', resp['access_token'])
