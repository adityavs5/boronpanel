import io
import shutil
import subprocess
import tarfile
import pytest
from daemon import cpanel_import as ci

pytestmark = pytest.mark.skipif(not shutil.which('zstd'), reason='zstd not installed')


def archive(tmp_path, name='hello.txt'):
    tar = tmp_path / 'a.tar'
    with tarfile.open(tar, 'w') as tf:
        info = tarfile.TarInfo(name)
        info.size = 3
        tf.addfile(info, io.BytesIO(b'abc'))
    compressed = tmp_path / 'a.zst'
    subprocess.run(['zstd', '-q', str(tar), '-o', str(compressed)], check=True)
    return compressed


def test_zstd_extracts_with_standard_checks(tmp_path):
    source = archive(tmp_path)
    out = tmp_path / 'out'
    out.mkdir()
    ci._extract_archive(source, out)
    assert (out / 'hello.txt').read_bytes() == b'abc'


def test_zstd_cannot_escape_extraction_directory(tmp_path):
    source = archive(tmp_path, '../escape')
    out = tmp_path / 'out'
    out.mkdir()
    with pytest.raises(ci.CpanelImportError):
        ci._extract_archive(source, out)
    assert not (tmp_path / 'escape').exists()


def test_zstd_expansion_is_bounded(tmp_path):
    source = archive(tmp_path)
    output = tmp_path / 'expanded'
    with pytest.raises(ci.CpanelImportError, match='limit'):
        ci._decompress_zstd(source, output, 10)
    assert not output.exists()


@pytest.mark.parametrize('target,allowed', [('./public_html', True), ('/etc/shadow', False), ('../../outside', False)])
def test_only_standard_da_private_html_alias_can_be_omitted(tmp_path, target, allowed):
    source = tmp_path / 'alias.tar'
    with tarfile.open(source, 'w') as tf:
        m = tarfile.TarInfo('domains/example.com/private_html')
        m.type = tarfile.SYMTYPE
        m.linkname = target
        tf.addfile(m)
    out = tmp_path / 'out'
    out.mkdir()
    if allowed:
        ci._extract_archive(source, out, directadmin=True)
        assert not list(out.iterdir())
    else:
        with pytest.raises(ci.CpanelImportError):
            ci._extract_archive(source, out, directadmin=True)
