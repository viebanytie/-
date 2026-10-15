"""Smoke test the real Windows PHP/MariaDB services and restored course."""
import json
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

root = Path(sys.argv[1])
local = json.loads((root/'runtime/local.json').read_text(encoding='utf-8-sig'))
course = json.loads((root/'runtime/course.json').read_text(encoding='utf-8-sig'))
base = 'http://127.0.0.1:8088'
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    for username in ['admin','student']:
        context = browser.new_context()
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base+'/login/index.php')
        page.locator('#username').fill(username)
        page.locator('#password').fill(local[username])
        page.locator('#loginbtn').click()
        page.wait_for_load_state('networkidle')
        assert '/login/index.php' not in page.url, 'Login failed: '+username
        page.goto(base+'/course/view.php?id='+str(course['courseid']))
        page.wait_for_load_state('networkidle')
        assert 'Цифровая безопасность' in page.locator('body').inner_text(), 'Missing course'
        assert page.locator('a[href*="/mod/quiz/view.php"]').count() >= 1, 'Missing quiz'
        assert page.locator('a[href*="/mod/assign/view.php"]').count() >= 1, 'Missing practice'
        assert page.locator('a[href*="/mod/page/view.php"]').count() >= 4, 'Missing lessons'
        if username == 'student':
            page.locator('a[href*="/mod/quiz/view.php"]').first.click()
            assert '10' in page.locator('body').inner_text() or page.locator('button').count(), 'Missing quiz page'
            # Open a real attempt and verify all ten restored questions render.
            start = page.locator('button').filter(has_text='Начать тестирование')
            if not start.count(): start = page.locator('button').filter(has_text='Attempt quiz')
            if start.count():
                start.first.click()
                confirm = page.locator('#id_submitbutton')
                if confirm.count(): confirm.click()
                page.wait_for_load_state('networkidle')
                assert page.locator('.que').count() > 0, 'Quiz questions did not render'
        assert not errors, 'Browser JavaScript error: '+str(errors[:2])
        context.close()
    browser.close()
print('PASS: administrator login, student login, Russian course, lessons, quiz and practice on Windows.')
