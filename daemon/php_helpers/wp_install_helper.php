<?php
// Boron: silent WordPress install helper (Phase 3 feature 2).
// Static, checked into git, deployed as a plain file under /opt/boron
// (world-readable/traversable, unlike /var/lib/boron which is
// deliberately locked to root:boron-api for the control-plane DB's
// sake -- this file must be readable by the *hosting account's own uid*,
// since it's executed via `runuser -u <account>`, so it cannot live
// under that tree). Invoked as:
//   php wp_install_helper.php <docroot> <site_url> <title> <admin_user> <admin_email> <admin_password>
// Values are passed via argv, never interpolated into this file's own
// source text -- avoids any PHP-string-escaping injection class entirely.
// Calls WordPress core's own wp_install() (wp-admin/includes/upgrade.php)
// -- the same function WP-CLI's `wp core install` wraps internally.
error_reporting(E_ERROR | E_PARSE);
list($docroot, $site_url, $title, $admin_user, $admin_email, $admin_password) = array_slice($argv, 1);

$parts = parse_url($site_url);
$_SERVER['HTTP_HOST'] = $parts['host'];
$_SERVER['SERVER_NAME'] = $parts['host'];
$_SERVER['REQUEST_URI'] = '/';
$_SERVER['SERVER_PORT'] = (isset($parts['scheme']) && $parts['scheme'] === 'https') ? 443 : 80;
$_SERVER['HTTPS'] = (isset($parts['scheme']) && $parts['scheme'] === 'https') ? 'on' : '';

define('WP_INSTALLING', true);
require_once rtrim($docroot, '/') . '/wp-load.php';
require_once ABSPATH . 'wp-admin/includes/upgrade.php';
require_once ABSPATH . 'wp-admin/includes/translation-install.php';

$result = wp_install($title, $admin_user, $admin_email, true, '', $admin_password);
echo json_encode(array('success' => true, 'result' => $result));
