# -*- coding: utf-8 -*-
"""Focused browser test for the current manager login flow and session persistence."""
import os, sys, json, time, subprocess, tempfile, urllib.request, urllib.error, sqlite3
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
PORT = 18950
URL = f'http://127.0.0.1:{PORT}/'
DB = os.path.join(TMP, 'data', 'masar.db')
RES = []

def check(name, condition, detail=''):
    RES.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + (f' [{detail}]' if detail and not condition else ''), flush=True)

env = {k: v for k, v in os.environ.items() if not k.startswith('WHATSAPP_')}
env.update(MASAR_DB=DB, MASAR_BACKUPS=os.path.join(TMP, 'backups'),
           MASAR_PORT=str(PORT), MASAR_HOST='127.0.0.1',
           MASAR_WORKER_INTERVAL='60', MASAR_ACCESS_CODE='CODE-123')
srv = None

def start():
    global srv
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, 'server.py')],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(URL + 'api/health', timeout=1):
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError('server did not start')

def stop():
    global srv
    if srv:
        srv.terminate()
        srv.wait(timeout=10)
        srv = None
        time.sleep(0.3)

def api(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(URL + 'api/' + path.lstrip('/'), data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

start()
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1300, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(URL)
        page.wait_for_selector('#loginOverlay')
        check('U01 registration asks for username, password, and activation code',
              page.locator('#newAccountName').count() == 1 and
              page.locator('#newAccountPassword').count() == 1 and
              page.locator('#newAccountCode').count() == 1)

        page.fill('#newAccountName', 'admin')
        page.fill('#newAccountPassword', 'Admin#12345')
        page.fill('#newAccountCode', 'WRONG')
        page.click('#createAccountBtn')
        page.wait_for_timeout(500)
        health = api('GET', 'health')[1]
        check('U02 wrong activation code does not create a manager',
              health.get('managerExists') is False and page.locator('#loginOverlay').count() == 1)

        page.fill('#newAccountCode', 'CODE-123')
        page.click('#createAccountBtn')
        page.wait_for_selector('#loginOverlay', state='detached', timeout=10000)
        token = page.evaluate("localStorage.getItem('masar_token')")
        check('U03 correct code creates the manager and signs in',
              bool(token) and api('GET', 'me', token=token)[0] == 200)
        c = sqlite3.connect(DB)
        remaining = c.execute('SELECT MAX(expires_at) FROM sessions').fetchone()[0] - time.time()
        c.close()
        check('U03b remembered session is issued for approximately two years', remaining > 700 * 24 * 3600)

        status, state = api('GET', 'state', token=token)
        check('U04 authenticated school state is loaded from server',
              status == 200 and isinstance(state.get('state'), dict))

        status, added = api('POST', 'students', {
            'name': 'طالب اختبار بقاء البيانات', 'age': 12, 'className': 'السادس أ',
            'guardianName': 'ولي الأمر', 'guardianPhone': '0910000000'
        }, token=token)
        check('U05 student record is saved through the authenticated API', status == 200, str(added)[:160])

        # Simulate a service restart while retaining the same database directory.
        stop()
        start()
        page.reload()
        page.wait_for_selector('#rememberedAccountBtn', timeout=10000)
        check('U06 existing session is recognized after server restart',
              page.locator('#rememberedAccountBtn').count() == 1)
        page.click('#rememberedAccountBtn')
        page.wait_for_selector('#navStudents, .nav-item', timeout=10000)
        page.wait_for_timeout(800)
        status, state = api('GET', 'state', token=token)
        names = [student.get('name') for student in state.get('state', {}).get('students', [])] if status == 200 else []
        check('U07 student and manager session remain after restart',
              'طالب اختبار بقاء البيانات' in names, str(names))

        # The stored account can also authenticate with its chosen password.
        page.evaluate("logout()")
        page.wait_for_selector('#loginOverlay', timeout=10000)
        page.click('#recoverAccountBtn')
        page.fill('#liUser', 'admin')
        page.fill('#liPass', 'Admin#12345')
        page.click('#liBtn')
        page.wait_for_selector('#loginOverlay', state='detached', timeout=10000)
        check('U08 username/password login works after restart',
              api('POST', 'login', {'username':'admin','password':'Admin#12345'})[0] == 200)
        check('U09 no browser JavaScript errors during login/restart flow', not errors, '; '.join(errors[:3]))
        browser.close()
finally:
    try:
        stop()
    except Exception:
        pass

# Exercise the migration helper against isolated temporary databases.
migration_tmp = Path(tempfile.mkdtemp())
legacy = migration_tmp / 'legacy.db'
target = migration_tmp / 'persistent' / 'masar.db'
db = sqlite3.connect(legacy)
db.execute("CREATE TABLE keep_me (value TEXT NOT NULL)")
db.execute("INSERT INTO keep_me VALUES ('school-record-123')")
db.commit()
db.close()
server.LEGACY_DB_PATH = legacy
server.DB_PATH = target
copied = server.migrate_legacy_database()
db = sqlite3.connect(target)
integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
value = db.execute("SELECT value FROM keep_me").fetchone()[0]
db.close()
check('P01 valid legacy SQLite database migrates with integrity intact',
      copied is True and integrity == 'ok' and value == 'school-record-123')
check('P02 migration preserves source and never overwrites an existing target',
      legacy.exists() and server.migrate_legacy_database() is False)
corrupt = migration_tmp / 'corrupt.db'
corrupt.write_bytes(b'not a sqlite database')
target2 = migration_tmp / 'persistent2' / 'masar.db'
server.LEGACY_DB_PATH = corrupt
server.DB_PATH = target2
try:
    server.migrate_legacy_database()
    corrupt_rejected = False
except (sqlite3.DatabaseError, RuntimeError):
    corrupt_rejected = True
check('P03 corrupt legacy database is rejected without empty replacement',
      corrupt_rejected and corrupt.exists() and not target2.exists())

failed = [name for name, passed in RES if not passed]
print(f'\\nTOTAL {len(RES)}  PASS {len(RES)-len(failed)}  FAIL {len(failed)}')
for name in failed:
    print('FAILED:', name)
sys.exit(1 if failed else 0)
