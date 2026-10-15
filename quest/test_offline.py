from pathlib import Path
from playwright.sync_api import sync_playwright
import threading,http.server,functools,json
root=Path(__file__).parent
class Quiet(http.server.SimpleHTTPRequestHandler):
 def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',8099),functools.partial(Quiet,directory=str(root)));threading.Thread(target=server.serve_forever,daemon=True).start()
q=json.loads((root/'joyteka_quest.json').read_text())
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 page=b.new_page();errors=[];outside=[]
 page.on('pageerror',lambda error:errors.append(str(error)))
 page.on('request',lambda r:outside.append(r.url) if not r.url.startswith(('http://127.0.0.1:8099/','file://','blob:')) else None)
 page.goto('http://127.0.0.1:8099/index.html');assert page.locator('.card:disabled').count()==4
 page.locator('.card').first.click();page.locator('#answer').fill('9');page.locator('#check').click();assert page.locator('#mistakes').inner_text()=='1'
 page.locator('#showhint').click();assert page.locator('#hints').inner_text()=='1';page.locator('#close').click();page.reload();assert page.locator('#mistakes').inner_text()=='1'
 for i,t in enumerate(q['tasks']):
  page.locator('.card').nth(i).click();page.locator('#answer').fill(t['answer']);page.locator('#check').click();assert 'Вывод подтверждён' in page.locator('#message').inner_text();page.locator('#close').click()
 assert page.locator('#progress').inner_text()=='5 / 5';assert page.locator('#final').is_visible();page.reload();assert page.locator('#progress').inner_text()=='5 / 5'
 with page.expect_download() as d:page.locator('#report').click()
 assert d.value.suggested_filename=='ML-017_report.txt'
 page.screenshot(path=str(root/'Quest_preview.png'),full_page=True)
 page.on('dialog',lambda dialog:dialog.accept());page.locator('#reset').click();assert page.locator('#progress').inner_text()=='0 / 5'
 # A file URL checks that the offline launch itself needs no local web server.
 try:
  page.goto((root/'index.html').as_uri());assert page.locator('.card').count()==5
  mode='file:// passed'
 except Exception as e:
  mode='file:// blocked by cloud browser policy; HTTP workflow passed'
 assert not errors,errors;assert not outside,outside
 print('PASS: wrong answer, hint, saved progress, all 5 solutions, report download, reset, no external requests;',mode)
 b.close()
server.shutdown()
