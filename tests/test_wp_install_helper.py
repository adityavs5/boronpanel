import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize('url',['http://example.com/blog','https://example.com/shop','http://www.example.com/blog','https://www.example.com'])
def test_php_installer_persists_exact_selected_address(tmp_path,url):
    php=shutil.which('php')
    if not php:pytest.skip('PHP CLI is required for helper integration test')
    root=tmp_path/'site';root.mkdir()
    (root/'wp-load.php').write_text("<?php define('ABSPATH', __DIR__.'/');")
    includes=root/'wp-admin/includes';includes.mkdir(parents=True)
    (includes/'translation-install.php').write_text('<?php')
    (includes/'upgrade.php').write_text('''<?php
function wp_install(...$args) { return ['user_id'=>1, 'password'=>$args[5]]; }
function update_option($name,$value) {
  $file=getenv('WP_TEST_OUTPUT');
  $options=file_exists($file)?json_decode(file_get_contents($file),true):[];
  $options[$name]=$value;file_put_contents($file,json_encode($options));
}
''')
    output=tmp_path/'options.json'
    run=subprocess.run([php,str(Path('daemon/php_helpers/wp_install_helper.php').resolve()),str(root),url,'Test','siteadmin','test@example.com'],
        input='Aa1!test-private-password',capture_output=True,text=True,env={**os.environ,'WP_TEST_OUTPUT':str(output)})
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)=={'success':True}
    assert 'Aa1!test-private-password' not in run.stdout
    assert json.loads(output.read_text())=={'siteurl':url,'home':url}
