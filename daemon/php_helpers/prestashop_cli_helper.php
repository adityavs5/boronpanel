<?php
// Execute PrestaShop's own CLI installer with arguments delivered over stdin.
// This file is installed in Boron's root-owned tree and run as the hosting
// account. Never echo the supplied options: they contain two passwords.
error_reporting(E_ERROR | E_PARSE);
if ($argc !== 2) {
    exit(2);
}
$installer = realpath($argv[1]);
if ($installer === false || basename($installer) !== 'index_cli.php' ||
    basename(dirname($installer)) !== 'install' || !is_file($installer)) {
    exit(2);
}
$input = stream_get_contents(STDIN, 16385);
if ($input === false || strlen($input) > 16384) {
    exit(2);
}
try {
    $options = json_decode($input, true, 32, JSON_THROW_ON_ERROR);
} catch (JsonException $e) {
    exit(2);
}
$allowed = ['domain', 'db_server', 'db_name', 'db_user', 'db_password',
    'email', 'firstname', 'lastname', 'password', 'language', 'country',
    'admin_dir', 'newsletter', 'send_email'];
if (!is_array($options) || array_keys($options) !== $allowed) {
    exit(2);
}
$arguments = [$installer];
foreach ($options as $name => $value) {
    if (!is_string($value) || str_contains($value, "\0")) {
        exit(2);
    }
    $arguments[] = '--' . $name . '=' . $value;
}
$argv = $arguments;
$argc = count($argv);
$_SERVER['argv'] = $argv;
$_SERVER['argc'] = $argc;
$_SERVER['SCRIPT_FILENAME'] = $installer;
chdir(dirname($installer));
require $installer;
