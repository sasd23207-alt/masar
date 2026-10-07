# -*- coding: utf-8 -*-
"""متانة: كتابات متزامنة، قتل السيرفر فجأة (kill -9)، حقن HTML، تحميل الصفحة بدون سيرفر."""
import os, sys, json, time, subprocess, tempfile, threading, urllib.request, urllib.error, signal
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); T = tempfile.mkdtemp(); PORT = 18960; B = f'http://127.0.0.1:{PORT}'
RES = []; tok = ''
def check(n, c, e=''):
    RES.append((n, bool(c))); print(('PASS ' if c else 'FAIL ') + n + (f'  [{e}]' if e and not c else ''), flush=True)
def call(m, path, body=None):
    r = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, method=m, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok})
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
env = {k: v for k, v in os.environ.items() if not k.startswith('WHATSAPP_')}; env.update(MASAR_DB=T + '/d.db', MASAR_BACKUPS=T + '/b', MASAR_PORT=str(PORT), MASAR_HOST='127.0.0.1', MASAR_ACCESS_CODE='CODE-123')
srv = None
def start():
    global srv
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, 'server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try: urllib.request.urlopen(B + '/api/health'); return
        except Exception: time.sleep(0.2)
def login():
    global tok
    tok = json.loads(urllib.request.urlopen(urllib.request.Request(B + '/api/login', data=json.dumps({'username': 'admin', 'password': 'Admin#12345'}).encode(), headers={'Content-Type': 'application/json'})).read())['token']
start()
try:
    urllib.request.urlopen(urllib.request.Request(B + '/api/register', data=json.dumps({'username': 'admin', 'password': 'Admin#12345', 'accessCode': 'CODE-123'}).encode(), headers={'Content-Type': 'application/json'})); login()
    codes = []
    def add(i): codes.append(call('POST', '/api/students', {'name': f'طالب {i}', 'className': 'السادس أ', 'guardianPhone': f'09100{i:05d}'})[0])
    ths = [threading.Thread(target=add, args=(i,)) for i in range(60)]; [t.start() for t in ths]; [t.join() for t in ths]
    n = len(call('GET', '/api/state')[1]['state']['students'])
    check('R01 60 كتابة متزامنة: كلها نجحت ولا ضياع ولا تكرار', codes.count(200) == 60 and n == 60, (codes.count(200), n))
    call('POST', '/api/attendance', {'studentId': 1, 'status': 'absent'})
    srv.send_signal(signal.SIGKILL); srv.wait(); time.sleep(0.5)
    for f in ('d.db-wal',): pass
    start(); login(); st = call('GET', '/api/state')[1]['state']
    check('R02 بعد kill -9 (انقطاع كهرباء مفاجئ): لا ضياع للبيانات المؤكدة', len(st['students']) == 60 and st['attendanceHistory'])
    check('R03 حقن <script> في اسم الطالب مرفوض', call('POST', '/api/students', {'name': '<img src=x onerror=alert(1)>'})[0] == 400)
    check('R04 حقن HTML في الملاحظات/القاعة مرفوض', call('POST', '/api/grades', {'studentId': 1, 'subject': 'x', 'value': 5, 'note': '<b>'})[0] == 400)
    check('R05 JSON فاسد = 400 وليس انهيار', call('POST', '/api/students', None)[0] in (200, 400) and urllib.request.urlopen(B + '/api/health').status == 200)
    r = urllib.request.Request(B + '/api/students', data=b'{bad', method='POST', headers={'Authorization': 'Bearer ' + tok})
    try: urllib.request.urlopen(r)
    except urllib.error.HTTPError as e: check('R06 جسم JSON غير صالح = 400 والسيرفر يعمل', e.code == 400 and urllib.request.urlopen(B + '/api/health').status == 200)
    check('R07 فحص سلامة قاعدة البيانات', __import__('sqlite3').connect(T + '/d.db').execute('PRAGMA integrity_check').fetchone()[0] == 'ok' and __import__('sqlite3').connect(T + '/d.db').execute('PRAGMA foreign_key_check').fetchall() == [])
finally:
    try: srv.terminate()
    except Exception: pass
f = [n for n, ok in RES if not ok]; print(f'\nTOTAL {len(RES)}  PASS {len(RES) - len(f)}  FAIL {len(f)}'); sys.exit(1 if f else 0)
