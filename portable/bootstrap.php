<?php
define('CLI_SCRIPT', true);
require(__DIR__.'/server/moodle/config.php');
require_once($CFG->dirroot.'/backup/util/includes/restore_includes.php');
require_once($CFG->dirroot.'/user/lib.php');
$admin = get_admin();
$DB->set_field('user', 'lang', 'ru', ['id'=>$admin->id]);
\core\session\manager::set_user($admin);
$course = $DB->get_record('course', ['shortname' => 'DIGITAL-SAFETY']);
if (!$course) {
    $tempdir = restore_controller::get_tempdir_name(SITEID, $admin->id);
    $path = make_backup_temp_directory($tempdir);
    get_file_packer('application/vnd.moodle.backup')->extract_to_pathname(__DIR__.'/Digital_safety_course.mbz', $path);
    $newcourse = restore_dbops::create_new_course('Цифровая безопасность: защити себя и свои данные', 'DIGITAL-SAFETY', 1);
    $rc = new restore_controller($tempdir, $newcourse, backup::INTERACTIVE_NO, backup::MODE_GENERAL, $admin->id, backup::TARGET_NEW_COURSE);
    if (!$rc->execute_precheck()) { throw new RuntimeException('Course restore precheck failed.'); }
    $rc->execute_plan();
    $rc->destroy();
    $course = $DB->get_record('course', ['id' => $newcourse], '*', MUST_EXIST);
    $DB->set_field('course', 'shortname', 'DIGITAL-SAFETY', ['id' => $newcourse]);
}
$local = json_decode(file_get_contents(__DIR__.'/runtime/local.json'), true, 512, JSON_THROW_ON_ERROR);
$student = $DB->get_record('user', ['username'=>'student','deleted'=>0]);
if (!$student) {
    $student = (object)['auth'=>'manual','confirmed'=>1,'mnethostid'=>$CFG->mnet_localhost_id,'username'=>'student',
        'password'=>$local['student'],'firstname'=>'Учебный','lastname'=>'Студент','email'=>'student@example.invalid',
        'lang'=>'ru','timezone'=>'Europe/Moscow'];
    $student->id = user_create_user($student);
}
$instance = $DB->get_record('enrol', ['courseid'=>$course->id,'enrol'=>'manual'], '*', MUST_EXIST);
$role = $DB->get_record('role', ['shortname'=>'student'], '*', MUST_EXIST);
enrol_get_plugin('manual')->enrol_user($instance, $student->id, $role->id);
set_config('registerauth', '');
set_config('timezone', 'Europe/Moscow');
set_config('lang', 'ru');
set_config('langmenu', 1);
set_config('enablecompletion', 1);
set_config('messaging', 0);
set_config('disableupdatenotifications', 1);
set_config('frontpage', '6');
set_config('frontpageloggedin', '6');
set_config('showcoursetags', 0);
unset_config('newadminuser');
$summary = ['courseid'=>(int)$course->id,'pages'=>$DB->count_records('page',['course'=>$course->id]),
    'quizzes'=>$DB->count_records('quiz',['course'=>$course->id]),'assignments'=>$DB->count_records('assign',['course'=>$course->id])];
if ($summary['pages'] < 4 || $summary['quizzes'] !== 1 || $summary['assignments'] !== 1) {
    throw new RuntimeException('Course content verification failed.');
}
file_put_contents(__DIR__.'/runtime/course.json', json_encode($summary));
file_put_contents(__DIR__.'/runtime/ready', 'ready');
echo "Course, quiz, practice and student enrolment are ready.\n";
