"""Cache content-addressed frontend assets while revalidating stable entry files."""
import re

from fastapi.staticfiles import StaticFiles


class PanelStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            if re.fullmatch(r'dist/assets/[^/]+-[A-Za-z0-9_-]{8}\.(?:js|css|woff2|svg|png|jpg|jpeg|webp|ico)', path):
                response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
            elif path in ('dist/index.html', 'dist/theme-init.js'):
                response.headers['Cache-Control'] = 'no-cache'
        return response
