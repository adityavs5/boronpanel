import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from daemon import appinstaller, ols, site_templates, wordpress
from daemon import domain_welcome as welcome
from shared.config import settings
from shared.models import Account


@pytest.fixture()
def site(tmp_path, monkeypatch):
    home = tmp_path / 'homes' / 'demo1'
    root = home / 'public_html'
    root.mkdir(parents=True)
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    monkeypatch.setattr(settings, 'home_base', str(home.parent))
    monkeypatch.setattr(settings, 'db_path', str(state / 'boron.db'))
    monkeypatch.setattr(welcome.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid()))
    monkeypatch.setattr(site_templates, 'render_domain_welcome', lambda domain: f'<!doctype html><title>{domain}</title>Welcome', raising=False)
    monkeypatch.setattr(welcome, '_cursor', '')
    return root


def seed(site):
    assert welcome.ensure_welcome('demo1', 'example.test', str(site))
    assert welcome.is_managed_welcome('demo1', str(site))
    return site / 'welcome.html'


def test_empty_new_domain_gets_welcome_not_an_index(site):
    (site / '.well-known/acme-challenge').mkdir(parents=True)
    page = seed(site)
    assert 'example.test' in page.read_text()
    assert not (site / 'index.html').exists()
    assert not (site / 'index.php').exists()
    assert not welcome.ensure_welcome('demo1', 'alias.test', str(site))
    assert len(list(welcome._state_dir().glob('*.json'))) == 1
    assert welcome._state_dir().stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('name', ['index.html', 'index.php', 'readme.txt', '.htaccess', 'welcome.html'])
def test_existing_customer_content_is_untouched(site, name):
    target = site / name
    target.write_text('Customer-owned content')
    assert not welcome.ensure_welcome('demo1', 'example.test', str(site))
    assert target.read_text() == 'Customer-owned content'
    assert not welcome.is_managed_welcome('demo1', str(site))


@pytest.mark.parametrize('name', ['index.html', 'index.php'])
def test_index_upload_retires_only_placeholder(site, name):
    page = seed(site)
    (site / name).write_text('Actual site')
    assert welcome.reconcile_pending() == {'checked': 1, 'removed': 1}
    assert not page.exists()
    assert (site / name).read_text() == 'Actual site'
    assert welcome.reconcile_pending() == {'checked': 0, 'removed': 0}


def test_non_index_upload_keeps_welcome_until_install_succeeds(site):
    page = seed(site)
    (site / 'README.txt').write_text('readme')
    (site / '.htaccess').write_text('rules')
    assert welcome.reconcile_pending() == {'checked': 1, 'removed': 0}
    assert page.exists()
    assert welcome.retire_welcome('demo1', str(site), installed=True)
    assert not page.exists()


@pytest.mark.parametrize('replace', [False, True])
def test_modified_or_replaced_customer_welcome_is_never_deleted(site, replace):
    page = seed(site)
    if replace:
        page.unlink()
    page.write_text('My own welcome')
    (site / 'index.php').write_text('site')
    assert not welcome.retire_welcome('demo1', str(site), installed=True)
    assert page.read_text() == 'My own welcome'
    assert not list(welcome._state_dir().glob('*.json'))


def test_symlink_or_hardlink_welcome_is_not_followed_or_removed(site):
    page = seed(site)
    outside = site.parent / 'private.txt'
    outside.write_text('Private customer file')
    page.unlink()
    page.symlink_to(outside)
    assert not welcome.retire_welcome('demo1', str(site), installed=True)
    assert page.is_symlink()
    assert outside.read_text() == 'Private customer file'
    page.unlink()
    page = seed(site)
    os.link(page, outside.with_name('other-link.html'))
    assert not welcome.retire_welcome('demo1', str(site), installed=True)
    assert page.exists()


def test_symlink_docroot_never_crosses_accounts(site):
    other = site.parents[1] / 'other1' / 'public_html'
    other.mkdir(parents=True)
    real = site.with_name('real')
    site.rename(real)
    site.symlink_to(other, target_is_directory=True)
    with pytest.raises((OSError, welcome.safeio.UnsafePathError)):
        welcome.ensure_welcome('demo1', 'example.test', str(site))
    assert not (other / 'welcome.html').exists()


def test_nested_custom_root_is_supported_without_touching_parent(site):
    custom = site.parent / 'sites' / 'my-site'
    custom.mkdir(parents=True)
    assert welcome.ensure_welcome('demo1', 'custom.test', str(custom))
    assert (custom / 'welcome.html').exists()
    assert not (site / 'welcome.html').exists()


def test_swapped_custom_file_is_restored_from_private_quarantine(site, monkeypatch):
    page = seed(site)
    rename = welcome._rename_noreplace
    first = True
    def replace_before_capture(source_fd, source, destination_fd, destination):
        nonlocal first
        if first:
            first = False
            page.unlink()
            page.write_text('Concurrent customer upload')
        return rename(source_fd, source, destination_fd, destination)
    monkeypatch.setattr(welcome, '_rename_noreplace', replace_before_capture)
    assert not welcome.retire_welcome('demo1', str(site), installed=True)
    assert page.read_text() == 'Concurrent customer upload'
    assert not list(site.glob('.boron-welcome-*'))


def test_racing_second_upload_preserves_both_custom_files(site, monkeypatch):
    page = seed(site)
    rename = welcome._rename_noreplace
    first = True
    def replace_and_refill(source_fd, source, destination_fd, destination):
        nonlocal first
        if first:
            first = False
            page.unlink()
            page.write_text('First custom upload')
            rename(source_fd, source, destination_fd, destination)
            page.write_text('Second custom upload')
            return
        return rename(source_fd, source, destination_fd, destination)
    monkeypatch.setattr(welcome, '_rename_noreplace', replace_and_refill)
    assert not welcome.retire_welcome('demo1', str(site), installed=True)
    assert page.read_text() == 'Second custom upload'
    copies = list(site.glob('welcome-preserved-*.html'))
    assert len(copies) == 1 and copies[0].read_text() == 'First custom upload'


def test_install_admission_ignores_only_managed_unchanged_page(site):
    page = seed(site)
    for module in (wordpress, appinstaller):
        assert module._docroot_is_empty_enough(str(site), 'demo1')
        assert not module._docroot_is_empty_enough(str(site))
    page.write_text('Customer page')
    for module in (wordpress, appinstaller):
        assert not module._docroot_is_empty_enough(str(site), 'demo1')


def test_real_indexes_have_priority_and_directory_listing_stays_off():
    account = Account(id=1, username='demo1', uid=2000, gid=2000, status='active', php_version='8.3')
    domain = {'id': 1, 'domain': 'example.test', 'docroot': '/home/demo1/public_html', 'ssl_status': 'none'}
    content = ols.render_vhost_conf(account, domain, suspended=False)
    assert 'indexFiles              index.html, index.php, welcome.html' in content
    assert 'autoIndex               0' in content
    suspended = ols.render_vhost_conf(account, domain, suspended=True)
    assert 'RewriteRule ^(.*)$ /index.html [L]' in suspended


def test_reconcile_is_bounded_and_round_robin(site):
    for index in range(3):
        root = site.parent / f'site{index}'
        root.mkdir()
        assert welcome.ensure_welcome('demo1', f'site{index}.test', str(root))
        (root / 'index.html').write_text('site')
    assert welcome.reconcile_pending(limit=2) == {'checked': 2, 'removed': 2}
    assert welcome.reconcile_pending(limit=2) == {'checked': 1, 'removed': 1}


def test_subdirectory_install_does_not_remove_parent_placeholder(site):
    page = seed(site)
    blog = site / 'blog'
    blog.mkdir()
    (blog / 'index.php').write_text('Blog')
    assert not welcome.retire_welcome('demo1', str(blog), installed=True)
    assert page.exists()
    assert welcome.reconcile_pending() == {'checked': 1, 'removed': 0}


def test_laravel_staging_preserves_acme_and_welcome(site, monkeypatch):
    import subprocess

    from scripts.app_files import execute
    page = seed(site)
    (site / '.well-known/acme-challenge').mkdir(parents=True)
    def composer(args, **kwargs):
        assert args[:2] == ['composer', 'create-project']
        project = Path(args[-1])
        assert not project.exists()
        assert project.parent.parent == site
        project.mkdir()
        (project / 'public').mkdir()
        (project / 'public/index.php').write_text('Laravel entry')
        (project / '.env').write_text('Example non-secret config')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', composer)
    execute('laravel-project', str(site))
    assert (site / 'public/index.php').read_text() == 'Laravel entry'
    assert (site / '.well-known/acme-challenge').is_dir()
    assert page.exists()
    assert welcome.retire_welcome('demo1', str(site), installed=True)


def test_laravel_publish_does_not_overwrite_concurrent_customer_file(site, monkeypatch):
    import subprocess

    from scripts.app_files import execute
    def composer(args, **kwargs):
        project = Path(args[-1])
        project.mkdir()
        (project / '.env').write_text('Generated defaults')
        (site / '.env').write_text('Customer config')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', composer)
    with pytest.raises(FileExistsError):
        execute('laravel-project', str(site))
    assert (site / '.env').read_text() == 'Customer config'
