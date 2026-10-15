<?php
if (PHP_SAPI !== 'cli') { http_response_code(403); exit(1); }
$local = json_decode(file_get_contents(__DIR__.'/runtime/local.json'), true, 512, JSON_THROW_ON_ERROR);
mysqli_report(MYSQLI_REPORT_OFF);
$db = @new mysqli('127.0.0.1','moodle',$local['database'],'moodle',3308);
if ($db->connect_errno) { exit(2); }
$r = @$db->query("SELECT value FROM mdl_config WHERE name='version'");
if (!$r || !$r->fetch_row()) { exit(3); }
echo "Moodle installed.\n";
