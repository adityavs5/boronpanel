from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.static_assets import PanelStaticFiles


def test_hashed_assets_cache_and_entrypoints_revalidate(tmp_path):
    files = ['dist/assets/index-Abc123_-.js', 'dist/assets/inter-Dx4kXJAl.woff2',
             'dist/assets/index-HaSh1234.css', 'dist/index.html', 'dist/theme-init.js',
             'dist/assets/unhashed.js', 'custom-logo.svg']
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    app = FastAPI()
    app.mount('/static', PanelStaticFiles(directory=tmp_path))
    with TestClient(app) as client:
        for name in files[:3]:
            response = client.get('/static/' + name)
            assert response.status_code == 200
            assert response.headers['cache-control'] == 'public, max-age=31536000, immutable'
            cached = client.get('/static/' + name, headers={'If-None-Match': response.headers['etag']})
            assert cached.status_code == 304
            assert cached.headers['cache-control'] == response.headers['cache-control']
        for name in files[3:5]:
            assert client.get('/static/' + name).headers['cache-control'] == 'no-cache'
        for name in files[5:]:
            assert 'immutable' not in client.get('/static/' + name).headers.get('cache-control', '')
        missing = client.get('/static/dist/assets/missing-HaSh1234.js')
        assert missing.status_code == 404
        assert 'immutable' not in missing.headers.get('cache-control', '')
