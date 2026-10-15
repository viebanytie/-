<?php
// Local, CLI-only database initialisation. Never served by the web server.
if (PHP_SAPI !== 'cli') { http_response_code(403); exit(1); }
$root = __DIR__;
$local = json_decode(file_get_contents($root.'/runtime/local.json'), true, 512, JSON_THROW_ON_ERROR);
mysqli_report(MYSQLI_REPORT_OFF);
$db = @new mysqli('127.0.0.1', 'root', $local['root'], '', 3308);
if ($db->connect_errno) {
    $db = @new mysqli('127.0.0.1', 'root', '', '', 3308);
}
if ($db->connect_errno) { exit(2); }
if (($argv[1] ?? '') === 'ping') { echo "database ready\n"; exit(0); }
mysqli_report(MYSQLI_REPORT_ERROR | MYSQLI_REPORT_STRICT);
$pass = $db->real_escape_string($local['database']);
$rootpass = $db->real_escape_string($local['root']);
$db->query("CREATE DATABASE IF NOT EXISTS moodle CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci");
foreach (['localhost', '127.0.0.1'] as $host) {
    $db->query("CREATE USER IF NOT EXISTS 'moodle'@'$host' IDENTIFIED BY '$pass'");
    $db->query("ALTER USER 'moodle'@'$host' IDENTIFIED BY '$pass'");
    $db->query("GRANT ALL PRIVILEGES ON moodle.* TO 'moodle'@'$host'");
}
// Remove factory anonymous access; the package binds only to IPv4 loopback.
$users = $db->query("SELECT User,Host FROM mysql.user WHERE User IN ('','root')")->fetch_all(MYSQLI_ASSOC);
foreach ($users as $user) {
    $host = $db->real_escape_string($user['Host']);
    if ($user['User'] === '') {
        $db->query("DROP USER IF EXISTS ''@'$host'");
    } else {
        $db->query("SET PASSWORD FOR 'root'@'$host' = PASSWORD('$rootpass')");
    }
}
$db->query('FLUSH PRIVILEGES');
echo "Local database configured.\n";
