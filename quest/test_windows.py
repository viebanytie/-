from pathlib import Path
import json,sys
from playwright.sync_api import sync_playwright
root=Path(__file__).parent.resolve();q=json.loads((root/'joyteka_quest.json').read_text(encoding='utf-8'))
with sync_playwright() as p:
 b=p.chromium.launch(channel='msedge',headless=True)
 page=b.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto((root/'index.html').as_uri());page.locator('.card').first.wait_for()
 assert page.locator('.card:disabled').count()==4
 for i,t in enumerate(q['tasks']):
  page.locator('.card').nth(i).click();page.locator('#answer').fill(t['answer']);page.locator('#check').click();assert 'Вывод подтверждён' in page.locator('#message').inner_text();page.locator('#close').click()
 assert page.locator('#final').is_visible();page.reload();assert page.locator('#progress').inner_text()=='5 / 5'
 assert not errors,errors
 print('PASS: offline quest file:// launch, five solutions, completion and saved progress on Windows Edge.')
 # The public Joyteka page must contain the saved room and all six materials.
 online=b.new_page();r=online.goto('https://joyteka.com/ru/101145854',timeout=90000);assert r.status==200
 raw=online.locator('#__NEXT_DATA__').text_content();data=json.loads(raw)['props']['pageProps']['activity']
 assert data['template']==28 and len(data['variants'][0]['tasks'])==6
 assert data['variants'][0]['answer']==''.join(t['answer'] for t in q['tasks'])
 assert all(t.get('content') for t in data['variants'][0]['tasks'])
 print('PASS: anonymous Joyteka page, correct room, six uploaded materials and final answer.')
 b.close()
