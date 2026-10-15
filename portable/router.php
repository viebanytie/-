<?php
// PHP's native router supports Moodle slash arguments and static assets.
$path = rawurldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) ?: '/');
if (preg_match('~(?:^|/)\.[^/]~', $path)) { http_response_code(404); exit; }
return false;
