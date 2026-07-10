# Vendored API-docs assets (Run A feature 8)

These are third-party UI bundles for the admin-only interactive API docs
(`/api/docs` Swagger UI, `/api/redoc` ReDoc), vendored here so the panel
renders **offline** with no CDN dependency — the same principle as the
self-hosted Inter font and the rest of `static/`.

| File | Source package | Version |
|---|---|---|
| `swagger-ui-bundle.js`, `swagger-ui.css`, `favicon-32x32.png` | `swagger-ui-dist` | 5.32.8 |
| `redoc.standalone.js` | `redoc` | 2.5.3 |

They are served same-origin via the `/static` mount and referenced from
`api/main.py`'s `swagger_ui`/`redoc_ui` routes. The two docs pages carry a
dedicated CSP (`script-src 'self' 'unsafe-inline'`) set in
`_security_headers`; every other page keeps `script-src 'none'`/`'self'`.

To refresh after a dependency bump:

```bash
cd frontend
npm install --no-save swagger-ui-dist@5 redoc@2
cp node_modules/swagger-ui-dist/{swagger-ui-bundle.js,swagger-ui.css,favicon-32x32.png} ../static/apidocs/
cp node_modules/redoc/bundles/redoc.standalone.js ../static/apidocs/
```
