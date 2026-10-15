<?php
if (PHP_SAPI !== 'cli') { http_response_code(403); exit(1); }
$local = json_decode(file_get_contents(__DIR__.'/runtime/local.json'), true, 512, JSON_THROW_ON_ERROR);
$argv = [__DIR__.'/server/moodle/admin/cli/install_database.php', '--agree-license', '--lang=en',
    '--adminuser=admin', '--adminpass='.$local['admin'], '--adminemail=admin@example.invalid',
    '--supportemail=admin@example.invalid', '--fullname=Учебная платформа: цифровая безопасность',
    '--shortname=DigitalSafety', '--summary=Готовый локальный курс цифровой безопасности.'];
$argc = count($argv);
$_SERVER['argv'] = $argv;
$_SERVER['argc'] = $argc;
require(__DIR__.'/server/moodle/admin/cli/install_database.php');
