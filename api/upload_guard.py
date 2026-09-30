"""Authenticate and bound upload streams before FastAPI parses multipart data."""
from contextlib import AsyncExitStack

from fastapi import Depends, HTTPException, params
from fastapi.dependencies.utils import get_dependant, solve_dependencies
from fastapi.routing import APIRoute
from starlette.formparsers import MultiPartException
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.security import Identity, get_identity, require_account_access, require_admin
from shared.config import settings


def _admission(identity: Identity = Depends(get_identity)):
    return identity


class BoundedUploadRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        if not self.body_field or not isinstance(self.body_field.field_info, params.File):
            return handler
        admission = get_dependant(path=self.path_format, call=_admission)

        async def guarded(request):
            async with AsyncExitStack() as stack:
                result = await solve_dependencies(
                    request=request, dependant=admission, body=None,
                    dependency_overrides_provider=self.dependency_overrides_provider,
                    async_exit_stack=stack, embed_body_fields=False,
                )
                if result.errors:
                    raise HTTPException(401, 'authentication required')
                identity = result.values['identity']
                if 'username' in request.path_params:
                    require_account_access(identity, request.path_params['username'])
                    maximum = 2 * 1024 * 1024 * 1024  # existing SQL import limit
                else:
                    require_admin(identity)
                    maximum = (settings.branding_max_upload_bytes if '/branding/' in self.path
                               else settings.cpanel_import_max_upload_bytes)
            # Bound the entire multipart body, including additional file parts.
            # Keep the endpoint's exact per-file budget and allow modest framing.
            maximum += 64 * 1024
            length = request.headers.get('content-length')
            if length is not None:
                try:
                    size = int(length)
                    if size < 0:
                        raise ValueError
                except ValueError:
                    raise HTTPException(400, 'invalid content length') from None
                if size > maximum:
                    raise HTTPException(413, 'upload exceeds the request size limit')
            receive = request._receive
            received = 0
            exceeded = False

            async def bounded_receive():
                nonlocal received, exceeded
                message = await receive()
                if message['type'] == 'http.request':
                    received += len(message.get('body', b''))
                    if received > maximum:
                        exceeded = True
                        # Starlette's multipart parser closes all spooled files
                        # for this exception, including a chunked upload abort.
                        raise MultiPartException('upload exceeds the request size limit')
                return message

            request._receive = bounded_receive
            try:
                return await handler(request)
            except (StarletteHTTPException, MultiPartException):
                if exceeded:
                    raise HTTPException(413, 'upload exceeds the request size limit') from None
                raise
        return guarded
