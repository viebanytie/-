<?php
if (PHP_SAPI !== 'cli') { exit(1); }
$local=json_decode(file_get_contents(__DIR__.'/runtime/local.json'),true,512,JSON_THROW_ON_ERROR);
$db=new mysqli('127.0.0.1','root',$local['root'],'',3308);
$db->query('SHUTDOWN');
