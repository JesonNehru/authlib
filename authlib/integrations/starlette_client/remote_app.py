import inspect
import logging

from authlib.common.urls import urlparse
from authlib.jose import JsonWebToken, jwk
from authlib.oidc.core import UserInfo, CodeIDToken, ImplicitIDToken
from starlette.responses import RedirectResponse
from .._client import BaseApp
from .._client import (
    MissingRequestTokenError,
    MissingTokenError,
)

__all__ = ['RemoteApp']

log = logging.getLogger(__name__)


class RemoteApp(BaseApp):
    """A RemoteApp for Starlette framework."""

    async def _load_server_metadata(self):
        if self._server_metadata_url:
            metadata = await self._fetch_server_metadata(self._server_metadata_url)
            self._server_metadata_url = None  # only load once
            self.server_metadata.update(metadata)
        return self.server_metadata

    async def _send_token_update(self, token, refresh_token=None, access_token=None):
        if inspect.iscoroutinefunction(self._update_token):
            await self._update_token(
                token,
                refresh_token=refresh_token,
                access_token=access_token,
            )
        elif callable(self._update_token):
            self._update_token(
                token,
                refresh_token=refresh_token,
                access_token=access_token,
            )

    def _generate_access_token_params(self, request):
        if self.request_token_url:
            return request.scope
        return {
            'code': request.query_params.get('code'),
            'state': request.query_params.get('state'),
        }

    async def _create_oauth1_authorization_url(self, client, authorization_endpoint, **kwargs):
        params = {}
        if self.request_token_params:
            params.update(self.request_token_params)
        token = await client.fetch_request_token(
            self.request_token_url, **params
        )
        log.debug('Fetch request token: {!r}'.format(token))
        url = client.create_authorization_url(authorization_endpoint, **kwargs)
        return {'url': url, 'request_token': token}

    async def create_authorization_url(self, redirect_uri=None, **kwargs):
        """Generate the authorization url and state for HTTP redirect.

        :param redirect_uri: Callback or redirect URI for authorization.
        :param kwargs: Extra parameters to include.
        :return: dict
        """
        metadata = await self._load_server_metadata()
        authorization_endpoint = self.authorize_url
        if not authorization_endpoint and not self.request_token_url:
            authorization_endpoint = metadata.get('authorization_endpoint')

        if not authorization_endpoint:
            raise RuntimeError('Missing "authorize_url" value')

        if self.authorize_params:
            kwargs.update(self.authorize_params)

        async with self._get_oauth_client(**metadata) as client:
            client.redirect_uri = redirect_uri

            if self.request_token_url:
                return await self._create_oauth1_authorization_url(
                    client, authorization_endpoint, **kwargs)
            else:
                return self._create_oauth2_authorization_url(
                    client, authorization_endpoint, **kwargs)

    async def authorize_redirect(self, request, redirect_uri=None, **kwargs):
        """Create a HTTP Redirect for Authorization Endpoint.

        :param request: Starlette Request instance.
        :param redirect_uri: Callback or redirect URI for authorization.
        :param kwargs: Extra parameters to include.
        :return: Starlette ``RedirectResponse`` instance.
        """
        rv = await self.create_authorization_url(redirect_uri, **kwargs)
        self.save_authorize_data(request, redirect_uri=redirect_uri, **rv)
        return RedirectResponse(rv['url'])

    async def authorize_access_token(self, request, **kwargs):
        """Fetch an access token.

        :param request: Starlette Request instance.
        :return: A token dict.
        """
        params = self.retrieve_access_token_params(request)
        params.update(kwargs)
        return await self.fetch_access_token(**params)

    async def fetch_access_token(self, redirect_uri=None, request_token=None, **params):
        """Fetch access token in one step.

        :param redirect_uri: Callback or Redirect URI that is used in
                             previous :meth:`authorize_redirect`.
        :param request_token: A previous request token for OAuth 1.
        :param params: Extra parameters to fetch access token.
        :return: A token dict.
        """
        metadata = await self._load_server_metadata()
        token_endpoint = self.access_token_url
        if not token_endpoint and not self.request_token_url:
            token_endpoint = metadata.get('token_endpoint')

        async with self._get_oauth_client(**metadata) as client:
            if self.request_token_url:
                client.redirect_uri = redirect_uri
                if request_token is None:
                    raise MissingRequestTokenError()
                # merge request token with verifier
                token = {}
                token.update(request_token)
                token.update(params)
                client.token = token
                kwargs = self.access_token_params or {}
                token = await client.fetch_access_token(token_endpoint, **kwargs)
                client.redirect_uri = None
            else:
                client.redirect_uri = redirect_uri
                kwargs = {}
                if self.access_token_params:
                    kwargs.update(self.access_token_params)
                kwargs.update(params)
                token = await client.fetch_token(token_endpoint, **kwargs)
            return token

    async def request(self, method, url, token=None, **kwargs):
        if self.api_base_url and not url.startswith(('https://', 'http://')):
            url = urlparse.urljoin(self.api_base_url, url)

        async with self._get_oauth_client() as client:
            if kwargs.get('withhold_token'):
                return await client.request(method, url, **kwargs)

            request = kwargs.pop('request', None)
            if token is None and request:
                if inspect.iscoroutinefunction(self._fetch_token):
                    token = await self._fetch_token(request)
                elif callable(self._fetch_token):
                    token = self._fetch_token(request)

            if token is None:
                raise MissingTokenError()

            client.token = token
            return await client.request(method, url, **kwargs)

    async def userinfo(self, **kwargs):
        """Fetch user info from ``userinfo_endpoint``."""
        metadata = await self._load_server_metadata()
        resp = await self.get(metadata['userinfo_endpoint'], **kwargs)
        data = resp.json()

        compliance_fix = metadata.get('userinfo_compliance_fix')
        if compliance_fix:
            if inspect.iscoroutinefunction(compliance_fix):
                data = await compliance_fix(self, data)
            else:
                data = compliance_fix(self, data)
        return UserInfo(data)

    async def parse_id_token(self, request, token, claims_options=None):
        """Return an instance of UserInfo from token's ``id_token``."""
        if 'id_token' not in token:
            return None

        nonce = self._get_session_data(request, 'nonce')
        claims_params = dict(
            nonce=nonce,
            client_id=self.client_id,
        )
        if 'access_token' in token:
            claims_params['access_token'] = token['access_token']
            claims_cls = CodeIDToken
        else:
            claims_cls = ImplicitIDToken

        metadata = await self._load_server_metadata()
        if claims_options is None and 'issuer' in metadata:
            claims_options = {'iss': {'values': [metadata['issuer']]}}

        alg_values = metadata.get('id_token_signing_alg_values_supported')
        if not alg_values:
            alg_values = ['RS256']

        jwk_set = await self._fetch_jwk_set()

        def load_key(header, payload):
            # TODO: reload jwk set if invalid
            return jwk.loads(jwk_set, header.get('kid'))

        jwt = JsonWebToken(alg_values)
        claims = jwt.decode(
            token['id_token'], key=load_key,
            claims_cls=claims_cls,
            claims_options=claims_options,
            claims_params=claims_params,
        )
        claims.validate(leeway=120)
        return UserInfo(claims)

    async def _fetch_jwk_set(self, force=False):
        metadata = await self._load_server_metadata()
        jwk_set = metadata.get('jwks')
        if jwk_set and not force:
            return jwk_set

        uri = metadata.get('jwks_uri')
        if not uri:
            raise RuntimeError('Missing "jwks_uri" in metadata')

        jwk_set = await self._fetch_server_metadata(uri)
        self.server_metadata['jwks'] = jwk_set
        return jwk_set

    async def _fetch_server_metadata(self, url):
        resp = await self.request('GET', url, withhold_token=True)
        return resp.json()
