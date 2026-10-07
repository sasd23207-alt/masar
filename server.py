# -*- coding: utf-8 -*-
"""مِسار — سيرفر واحد، قاعدة SQLite واحدة (مصدر الحقيقة)، Worker للإشعارات.
بدون مكتبات خارجية (Python 3.9+)."""
import os, sys, json, sqlite3, threading, time, hashlib, hmac, secrets, re, shutil, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load_env():
    f = ROOT / '.env'
    if f.exists():
        for line in f.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
load_env()

DB_PATH = Path(os.environ.get('MASAR_DB', ROOT / 'data' / 'masar.db'))
BACKUP_DIR = Path(os.environ.get('MASAR_BACKUPS', ROOT / 'backups'))
HOST = os.environ.get('MASAR_HOST', '0.0.0.0')
PORT = int(os.environ.get('MASAR_PORT') or os.environ.get('PORT') or '8787')   # الاستضافات تمرر PORT
WA_TOKEN = lambda: os.environ.get('WHATSAPP_ACCESS_TOKEN', '')
WA_PHONE_ID = lambda: os.environ.get('WHATSAPP_PHONE_NUMBER_ID', '')
WA_GRAPH = lambda: os.environ.get('WHATSAPP_GRAPH_URL', 'https://graph.facebook.com/v23.0')
WA_TEMPLATE = lambda: os.environ.get('WHATSAPP_TEMPLATE_NAME', '')
WA_MODE = lambda: os.environ.get('WHATSAPP_MODE', 'template')  # template | text
COUNTRY_CODE = lambda: os.environ.get('MASAR_COUNTRY_CODE', '218').lstrip('+')
WORKER_INTERVAL = lambda: float(os.environ.get('MASAR_WORKER_INTERVAL', '30'))
OVERDUE_REMINDER_DAYS = lambda: int(os.environ.get('MASAR_OVERDUE_REMINDER_DAYS', '7'))
SESSION_HOURS = 12
DEFAULT_CLASSES = ['السادس أ','السابع أ','الثامن أ','التاسع أ','العاشر أ','الحادي عشر أ','الثاني عشر أ']

wlock = threading.RLock()   # يسلسل الكتابات (مع WAL لا تعطل القراءة)

def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
def today(): return date.today().isoformat()

# ---------------------------------------------------------------- DB / migrations
MIGRATIONS = [
("""
CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK(role IN ('admin','management','teacher','accountant','read_only')),
  salt TEXT NOT NULL, pass_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at REAL NOT NULL);
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE guardians(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE classes(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, grade TEXT NOT NULL, section TEXT NOT NULL);
CREATE TABLE teachers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
  classes_text TEXT NOT NULL DEFAULT '', salary REAL NOT NULL DEFAULT 0, salary_paid_at TEXT, archived_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE students(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, age INTEGER, class_id INTEGER REFERENCES classes(id),
  guardian_id INTEGER REFERENCES guardians(id), phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
  archived_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX idx_students_name ON students(name);
CREATE TABLE lessons(id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL, teacher_id INTEGER REFERENCES teachers(id) ON DELETE SET NULL,
  teacher_name TEXT NOT NULL DEFAULT '', class_id INTEGER NOT NULL REFERENCES classes(id), room TEXT NOT NULL, day TEXT NOT NULL,
  start_time TEXT NOT NULL, end_time TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE attendance(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  date TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('present','absent','late')), marked_by TEXT, updated_at TEXT NOT NULL,
  UNIQUE(student_id,date));
CREATE TABLE installments(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  series_id TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 1, total REAL NOT NULL CHECK(total>0), discount REAL NOT NULL DEFAULT 0, paid REAL NOT NULL DEFAULT 0,
  due_date TEXT NOT NULL, interval_months INTEGER NOT NULL DEFAULT 0, auto_renew INTEGER NOT NULL DEFAULT 0, renewed INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, closed_at TEXT);
CREATE TABLE payments(id INTEGER PRIMARY KEY AUTOINCREMENT, installment_id INTEGER NOT NULL REFERENCES installments(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE, amount REAL NOT NULL CHECK(amount>0), note TEXT NOT NULL DEFAULT '',
  paid_at TEXT NOT NULL, by_user TEXT);
CREATE TABLE grades(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  subject TEXT NOT NULL, value REAL NOT NULL CHECK(value>=0 AND value<=100), note TEXT NOT NULL DEFAULT '', date TEXT NOT NULL, by_user TEXT, created_at TEXT NOT NULL);
CREATE INDEX idx_grades_student ON grades(student_id);
CREATE TABLE notification_outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
  installment_id INTEGER REFERENCES installments(id) ON DELETE CASCADE, phone TEXT NOT NULL, message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','failed','cancelled')), dedupe_key TEXT NOT NULL UNIQUE,
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT, provider_response TEXT,
  cancel_reason TEXT, created_at TEXT NOT NULL, sent_at TEXT);
CREATE INDEX idx_outbox_status ON notification_outbox(status);
CREATE TABLE audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, user TEXT, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
""",),
("""
ALTER TABLE notification_outbox ADD COLUMN wa_message_id TEXT;
CREATE INDEX idx_outbox_wamid ON notification_outbox(wa_message_id);
CREATE TABLE inbound_messages(id INTEGER PRIMARY KEY AUTOINCREMENT, wa_message_id TEXT NOT NULL UNIQUE, from_phone TEXT NOT NULL,
  student_id INTEGER REFERENCES students(id) ON DELETE SET NULL, profile_name TEXT NOT NULL DEFAULT '', msg_type TEXT NOT NULL DEFAULT 'text',
  body TEXT NOT NULL DEFAULT '', received_at TEXT NOT NULL);
CREATE INDEX idx_inbound_phone ON inbound_messages(from_phone);
""",),
]

def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=FULL')
    return c

def migrate():
    c = conn()
    c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, at TEXT NOT NULL)')
    cur = (c.execute('SELECT MAX(version) m FROM schema_migrations').fetchone()['m'] or 0)
    for i, (sql,) in enumerate(MIGRATIONS, 1):
        if i > cur:
            c.executescript('BEGIN;' + sql + f"INSERT INTO schema_migrations VALUES({i},'{now()}');COMMIT;")
    # الصفوف (بنية وليست بيانات تجريبية)
    for n in DEFAULT_CLASSES: ensure_class(c, n)
    c.close()

def split_class(name):
    parts = name.strip().split(' ')
    return (' '.join(parts[:-1]), parts[-1]) if len(parts) > 1 else (name.strip(), 'أ')

def ensure_class(c, name):
    name = re.sub(r'\s+', ' ', name.strip())
    r = c.execute('SELECT id FROM classes WHERE name=?', (name,)).fetchone()
    if r: return r['id']
    g, s = split_class(name)
    return c.execute('INSERT INTO classes(name,grade,section) VALUES(?,?,?)', (name, g, s)).lastrowid

def audit(c, user, action, detail=''):
    c.execute('INSERT INTO audit_log(at,user,action,detail) VALUES(?,?,?,?)', (now(), user, action, json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail))

# ---------------------------------------------------------------- auth
ROLES = {
 # صلاحيات لكل دور
 'admin':      {'students','teachers','attendance','finance','grades','lessons','reports','users','backup','delete','outbox'},
 'management': {'students','teachers','attendance','finance','grades','lessons','reports','outbox'},
 'teacher':    {'attendance','grades','students_read','lessons_read','reports_own'},
 'accountant': {'finance','students_read','reports_fin','outbox'},
 'read_only':  {'students_read','lessons_read','reports_read'},
}
def can(role, perm): return perm in ROLES.get(role, ())
def hash_pw(pw, salt): return hashlib.pbkdf2_hmac('sha256', pw.encode(), bytes.fromhex(salt), 200_000).hex()
def create_user(c, username, password, role, full_name=''):
    if role not in ROLES: raise ApiError(400, 'ROLE_INVALID')
    if len(password) < 8: raise ApiError(400, 'PASSWORD_TOO_SHORT')
    if not username.strip(): raise ApiError(400, 'USERNAME_REQUIRED')
    salt = secrets.token_hex(16)
    try:
        return c.execute('INSERT INTO users(username,full_name,role,salt,pass_hash,created_at) VALUES(?,?,?,?,?,?)',
                         (username.strip(), full_name, role, salt, hash_pw(password, salt), now())).lastrowid
    except sqlite3.IntegrityError: raise ApiError(409, 'USERNAME_EXISTS')
def login(c, username, password):
    u = c.execute('SELECT * FROM users WHERE username=? AND active=1', (username.strip(),)).fetchone()
    if not u or not hmac.compare_digest(u['pass_hash'], hash_pw(password, u['salt'])): return None
    tok = secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(tok.encode()).hexdigest(), u['id'], time.time() + SESSION_HOURS * 3600))
    c.execute('DELETE FROM sessions WHERE expires_at<?', (time.time(),))
    return tok, u
def user_from_token(c, tok):
    if not tok: return None
    return c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1',
                     (hashlib.sha256(tok.encode()).hexdigest(), time.time())).fetchone()

def check_text(v, key=''):
    """منع إدخال أكواد HTML/JS في أي حقل نصي (الواجهة تعرض النصوص كما هي) — كلمات المرور مستثناة."""
    if key == 'password': return
    if isinstance(v, str):
        if '<' in v or '>' in v: raise ApiError(400, 'INVALID:characters')
    elif isinstance(v, dict):
        for k, x in v.items(): check_text(x, k)
    elif isinstance(v, list):
        for x in v: check_text(x, key)

ACCESS_CODE = lambda: os.environ.get('MASAR_ACCESS_CODE', '')
_fails = {}   # ip -> (عدد, وقت أول فشل)
def throttle_check(ip):
    n, t0 = _fails.get(ip, (0, 0))
    if n >= 5 and time.time() - t0 < 300: raise ApiError(429, 'TOO_MANY_ATTEMPTS')
    if time.time() - t0 >= 300: _fails.pop(ip, None)
def throttle_fail(ip):
    n, t0 = _fails.get(ip, (0, time.time())); _fails[ip] = (n + 1, t0 if n else time.time())
def throttle_ok(ip): _fails.pop(ip, None)

class ApiError(Exception):
    def __init__(self, code, msg): self.code, self.msg = code, msg

# ---------------------------------------------------------------- helpers
def normalize_phone(p):
    d = re.sub(r'[^0-9+]', '', str(p or ''))
    if not d or d in ('+',): return ''
    if d.startswith('+'): return d[1:]
    if d.startswith('00'): return d[2:]
    if d.startswith('0'):
        cc = COUNTRY_CODE()
        return (cc + d[1:]) if cc else ''
    return d

def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12); y += d.year; m += 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))

def need(body, *keys):
    for k in keys:
        if body.get(k) in (None, ''): raise ApiError(400, f'MISSING:{k}')
def num(v, field, minimum=None):
    try: x = float(v)
    except (TypeError, ValueError): raise ApiError(400, f'INVALID:{field}')
    if x != x or x in (float('inf'), float('-inf')) or (minimum is not None and x < minimum): raise ApiError(400, f'INVALID:{field}')
    return x
def vdate(s, field):
    try: return date.fromisoformat(str(s)).isoformat()
    except Exception: raise ApiError(400, f'INVALID:{field}')
def fmt(n): return f'{n:,.2f}'.rstrip('0').rstrip('.') if n % 1 else f'{int(n):,}'

# ---------------------------------------------------------------- outbox
def queue(c, event, key, phone_raw, message, student_id=None, installment_id=None):
    phone = normalize_phone(phone_raw)
    if not phone: return False
    try:
        c.execute('INSERT INTO notification_outbox(event,student_id,installment_id,phone,message,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?)',
                  (event, student_id, installment_id, phone, message, key, now())); return True
    except sqlite3.IntegrityError: return False   # نفس الحدث موجود: لا تكرار

def student_guardian_phone(c, sid):
    r = c.execute('SELECT g.phone FROM students s LEFT JOIN guardians g ON g.id=s.guardian_id WHERE s.id=?', (sid,)).fetchone()
    return (r['phone'] if r else '') or ''

def inst_remaining(i): return max(0.0, i['total'] - i['discount'] - i['paid'])

def cancel_overdue(c, inst_id, reason):
    c.execute("UPDATE notification_outbox SET status='cancelled',cancel_reason=? WHERE installment_id=? AND event='PAYMENT_OVERDUE' AND status='pending'", (reason, inst_id))

def scan_overdue(c):
    """يُنشئ إشعارات التأخير للأقساط المتأخرة فقط (متبقي>0). ويلغي أي إشعار تأخير لقسط مسدد."""
    t = today(); added = 0
    for i in c.execute('SELECT i.*, s.name sname FROM installments i JOIN students s ON s.id=i.student_id WHERE s.archived_at IS NULL').fetchall():
        if inst_remaining(i) <= 0:
            cancel_overdue(c, i['id'], 'installment_paid'); continue
        if i['due_date'] >= t: continue
        days_late = (date.fromisoformat(t) - date.fromisoformat(i['due_date'])).days
        r = OVERDUE_REMINDER_DAYS()
        bucket = 0 if r <= 0 else days_late // r
        phone = student_guardian_phone(c, i['student_id'])
        msg = (f"السلام عليكم ولي أمر الطالب {i['sname']}، نحيطكم علمًا بأن القسط المستحق بتاريخ {i['due_date']} متأخر، "
               f"والمبلغ المتبقي {fmt(inst_remaining(i))}. يرجى مراجعة إدارة المدرسة لتسوية المبلغ.")
        if queue(c, 'PAYMENT_OVERDUE', f"PAYMENT_OVERDUE|{i['id']}|{bucket}", phone, msg, i['student_id'], i['id']): added += 1
    return added

def renew_if_needed(c, inst_id, user):
    """قسط سُدد بالكامل + تجديد تلقائي => قسط جديد للفترة التالية + رسالة لولي الأمر."""
    i = c.execute('SELECT * FROM installments WHERE id=?', (inst_id,)).fetchone()
    if not i or not i['auto_renew'] or i['interval_months'] <= 0 or i['renewed'] or inst_remaining(i) > 0: return None
    nd = add_months(date.fromisoformat(i['due_date']), i['interval_months']).isoformat()
    nid = c.execute('INSERT INTO installments(student_id,series_id,seq,total,discount,paid,due_date,interval_months,auto_renew,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (i['student_id'], i['series_id'], i['seq'] + 1, i['total'], 0, 0, nd, i['interval_months'], 1, now())).lastrowid
    c.execute('UPDATE installments SET renewed=1 WHERE id=?', (inst_id,))
    s = c.execute('SELECT name FROM students WHERE id=?', (i['student_id'],)).fetchone()
    msg = (f"السلام عليكم ولي أمر الطالب {s['name']}، تم سداد القسط بالكامل، شكرًا لكم. "
           f"القسط القادم بقيمة {fmt(i['total'])} وآخر موعد للسداد {nd}.")
    queue(c, 'INSTALLMENT_RENEWED', f"INSTALLMENT_RENEWED|{nid}", student_guardian_phone(c, i['student_id']), msg, i['student_id'], nid)
    audit(c, user, 'installment_renewed', {'from': inst_id, 'to': nid, 'due': nd})
    return {'id': nid, 'dueDate': nd, 'amount': i['total'], 'message': msg}

# ---------------------------------------------------------------- WhatsApp adapter
def wa_configured(): return bool(WA_TOKEN() and WA_PHONE_ID() and (WA_MODE() == 'text' or WA_TEMPLATE()))

def send_whatsapp(item):
    if not wa_configured(): return False, 'WHATSAPP_NOT_CONFIGURED'
    text = re.sub(r'\s+', ' ', item['message']).strip()
    if WA_MODE() == 'text':
        payload = {'messaging_product': 'whatsapp', 'to': item['phone'], 'type': 'text', 'text': {'body': item['message']}}
    else:
        payload = {'messaging_product': 'whatsapp', 'to': item['phone'], 'type': 'template',
                   'template': {'name': WA_TEMPLATE(), 'language': {'code': os.environ.get('WHATSAPP_TEMPLATE_LANG', 'ar')},
                                'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': text[:1000]}]}]}}
    req = urllib.request.Request(f"{WA_GRAPH()}/{WA_PHONE_ID()}/messages", data=json.dumps(payload).encode(),
                                 headers={'Authorization': f'Bearer {WA_TOKEN()}', 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300, r.read().decode(errors='replace')
    except urllib.error.HTTPError as e: return False, f'HTTP {e.code}: ' + e.read().decode(errors='replace')[:500]
    except Exception as e: return False, str(e)

def deliver_pending(c, limit=20):
    if not wa_configured(): return 0
    sent = 0
    rows = c.execute("SELECT * FROM notification_outbox WHERE status='pending' AND next_attempt_at<=? ORDER BY id LIMIT ?", (time.time(), limit)).fetchall()
    for it in rows:
        # إعادة تحقق قبل الإرسال: القسط سُدد؟ الغياب أُلغي؟
        if it['event'] == 'PAYMENT_OVERDUE':
            i = c.execute('SELECT * FROM installments WHERE id=?', (it['installment_id'],)).fetchone()
            if not i or inst_remaining(i) <= 0:
                c.execute("UPDATE notification_outbox SET status='cancelled',cancel_reason='installment_paid' WHERE id=?", (it['id'],)); continue
        ok, detail = send_whatsapp(it)
        att = it['attempts'] + 1
        if ok:
            try: wamid = json.loads(detail)['messages'][0]['id']
            except Exception: wamid = None
            c.execute("UPDATE notification_outbox SET status='sent',attempts=?,sent_at=?,provider_response=?,last_error=NULL,wa_message_id=? WHERE id=?", (att, now(), detail[:1000], wamid, it['id'])); sent += 1
        else:
            st = 'failed' if att >= 5 else 'pending'
            c.execute('UPDATE notification_outbox SET status=?,attempts=?,last_error=?,next_attempt_at=? WHERE id=?', (st, att, detail[:1000], time.time() + min(3600, 30 * 2 ** (att - 1)), it['id']))
    return sent

def worker_tick():
    with wlock:
        c = conn()
        try:
            c.execute('BEGIN IMMEDIATE'); scan_overdue(c); c.execute('COMMIT')
            deliver_pending(c)
        except Exception as e:
            try: c.execute('ROLLBACK')
            except Exception: pass
            print('worker error:', e, file=sys.stderr)
        finally: c.close()

def worker():
    while True:
        worker_tick(); time.sleep(WORKER_INTERVAL())

# ---------------------------------------------------------------- state assembly (قراءة فقط من الجداول)
def student_row(r):
    cn = r['class_name'] or ''
    g, s = (r['grade'] or '', r['section'] or '')
    return {'id': r['id'], 'name': r['name'], 'age': r['age'], 'grade': g, 'section': s, 'className': cn, 'floor': '—',
            'phone': r['phone'] or '—', 'guardianName': r['guardian_name'] or '', 'guardianPhone': r['guardian_phone'] or '',
            'address': r['address'] or '—', 'archivedAt': r['archived_at'], 'createdAt': r['created_at']}

STUDENT_SQL = ('SELECT s.*, c.name class_name, c.grade, c.section, g.name guardian_name, g.phone guardian_phone '
               'FROM students s LEFT JOIN classes c ON c.id=s.class_id LEFT JOIN guardians g ON g.id=s.guardian_id')

def build_state(c, role):
    st = {'role': role, 'classes': [r['name'] for r in c.execute('SELECT name FROM classes ORDER BY id')]}
    st['students'] = [student_row(r) for r in c.execute(STUDENT_SQL + ' ORDER BY s.id')]
    st['teachers'] = [{'id': r['id'], 'tid': f"T-{1000 + r['id']}", 'name': r['name'], 'subject': r['subject'] or '—', 'phone': r['phone'] or '—',
                       'classes': [x for x in re.split(r'[،,]', r['classes_text']) if x.strip()] or ['—'], 'school': get_setting(c, 'schoolName'),
                       'salary': r['salary'], 'salaryPaidAt': r['salary_paid_at'], 'archivedAt': r['archived_at']} for r in c.execute('SELECT * FROM teachers ORDER BY id')]
    st['courses'] = [{'id': r['id'], 'title': r['subject'], 'teacher': r['teacher_name'], 'className': r['cname'], 'room': r['room'], 'day': r['day'],
                      'startTime': r['start_time'], 'endTime': r['end_time']} for r in
                     c.execute('SELECT l.*, c.name cname FROM lessons l JOIN classes c ON c.id=l.class_id ORDER BY l.id')]
    hist = {}
    for r in c.execute('SELECT student_id,date,status FROM attendance ORDER BY date'): hist.setdefault(r['date'], {})[str(r['student_id'])] = r['status']
    st['attendanceHistory'] = hist
    st['gradesData'] = [{'id': r['id'], 'studentId': r['student_id'], 'student': r['sname'], 'subject': r['subject'], 'val': r['value'], 'note': r['note'], 'date': r['date']}
                        for r in c.execute('SELECT g.*, s.name sname FROM grades g JOIN students s ON s.id=g.student_id ORDER BY g.id DESC')] if (can(role, 'grades') or can(role, 'students_read') or can(role, 'reports_read')) else []
    if can(role, 'finance') or can(role, 'reports_fin'):
        st['dues'] = [{'id': r['id'], 'studentId': r['student_id'], 'student': r['sname'], 'totalAmount': r['total'], 'discount': r['discount'], 'paidAmount': r['paid'],
                       'dueDate': r['due_date'], 'intervalMonths': r['interval_months'], 'autoRenew': bool(r['auto_renew']), 'seq': r['seq']}
                      for r in c.execute('SELECT i.*, s.name sname FROM installments i JOIN students s ON s.id=i.student_id ORDER BY i.id')]
        st['transactions'] = [{'id': r['id'], 'studentId': r['student_id'], 'student': r['sname'], 'amount': r['amount'], 'note': r['note'], 'date': r['paid_at'][:10]}
                              for r in c.execute('SELECT p.*, s.name sname FROM payments p JOIN students s ON s.id=p.student_id ORDER BY p.id DESC')]
    else: st['dues'] = []; st['transactions'] = []
    if can(role, 'outbox'):
        st['notificationOutbox'] = [{k: r[k] for k in ('id', 'event', 'phone', 'message', 'status', 'attempts', 'last_error', 'created_at', 'sent_at', 'cancel_reason', 'installment_id', 'student_id')}
                                    for r in c.execute('SELECT * FROM notification_outbox ORDER BY id DESC LIMIT 500')]
    else: st['notificationOutbox'] = []
    st['settings'] = {'schoolName': get_setting(c, 'schoolName'), 'systemName': 'مِسار'}
    st['whatsappConfigured'] = wa_configured()
    return st

def get_setting(c, k):
    r = c.execute('SELECT value FROM settings WHERE key=?', (k,)).fetchone()
    return r['value'] if r else ('اسم المدرسة' if k == 'schoolName' else '')

# ---------------------------------------------------------------- operations (كل واحدة داخل معاملة)
def guardian_upsert(c, name, phone_raw):
    ph = normalize_phone(phone_raw)
    if not ph: return None
    r = c.execute('SELECT id,name FROM guardians WHERE phone=?', (ph,)).fetchone()
    if r:
        if name and not r['name']: c.execute('UPDATE guardians SET name=? WHERE id=?', (name, r['id']))
        return r['id']
    return c.execute('INSERT INTO guardians(name,phone,created_at) VALUES(?,?,?)', (name or '', ph, now())).lastrowid

def class_from(c, body):
    cn = (body.get('className') or '').strip()
    if not cn and body.get('grade'): cn = f"{body['grade']} {body.get('section') or 'أ'}"
    return ensure_class(c, cn) if cn else None

def op_student_add(c, u, b):
    need(b, 'name'); name = re.sub(r'\s+', ' ', str(b['name']).strip())
    if not name: raise ApiError(400, 'MISSING:name')
    cid = class_from(c, b); gid = guardian_upsert(c, b.get('guardianName', ''), b.get('guardianPhone', ''))
    if b.get('guardianPhone') and not gid: raise ApiError(400, 'INVALID:guardianPhone')
    age = int(num(b['age'], 'age', 0)) if b.get('age') not in (None, '') else None
    dup = c.execute('SELECT id FROM students WHERE name=? AND archived_at IS NULL AND IFNULL(class_id,0)=IFNULL(?,0) AND IFNULL(guardian_id,0)=IFNULL(?,0)', (name, cid, gid)).fetchone()
    if dup: raise ApiError(409, 'DUPLICATE_STUDENT')
    sid = c.execute('INSERT INTO students(name,age,class_id,guardian_id,phone,address,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                    (name, age, cid, gid, b.get('phone') or '', b.get('address') or '', now(), now())).lastrowid
    audit(c, u['username'], 'student_add', {'id': sid, 'name': name}); return {'id': sid}

def op_student_update(c, u, sid, b):
    r = c.execute('SELECT * FROM students WHERE id=?', (sid,)).fetchone()
    if not r: raise ApiError(404, 'NOT_FOUND')
    name = re.sub(r'\s+', ' ', str(b.get('name', r['name'])).strip()) or r['name']
    cid = class_from(c, b) if (b.get('className') or b.get('grade')) else r['class_id']
    gid = guardian_upsert(c, b.get('guardianName', ''), b['guardianPhone']) if 'guardianPhone' in b and b['guardianPhone'] else r['guardian_id']
    age = int(num(b['age'], 'age', 0)) if b.get('age') not in (None, '') else r['age']
    c.execute('UPDATE students SET name=?,age=?,class_id=?,guardian_id=?,phone=?,address=?,updated_at=? WHERE id=?',
              (name, age, cid, gid, b.get('phone', r['phone']), b.get('address', r['address']), now(), sid))
    audit(c, u['username'], 'student_update', {'id': sid}); return {'id': sid}

def op_student_archive(c, u, sid, restore=False):
    if not c.execute('SELECT 1 FROM students WHERE id=?', (sid,)).fetchone(): raise ApiError(404, 'NOT_FOUND')
    c.execute('UPDATE students SET archived_at=? WHERE id=?', (None if restore else now(), sid)); audit(c, u['username'], 'student_restore' if restore else 'student_archive', {'id': sid}); return {'id': sid}

def op_student_delete(c, u, sid):
    r = c.execute('SELECT name FROM students WHERE id=?', (sid,)).fetchone()
    if not r: raise ApiError(404, 'NOT_FOUND')
    c.execute('DELETE FROM students WHERE id=?', (sid,)); audit(c, u['username'], 'student_hard_delete', {'id': sid, 'name': r['name']}); return {'deleted': sid}

def op_teacher_add(c, u, b):
    need(b, 'name')
    tid = c.execute('INSERT INTO teachers(name,subject,phone,classes_text,salary,created_at) VALUES(?,?,?,?,?,?)',
                    (b['name'].strip(), b.get('subject', '') or '', b.get('phone', '') or '', b.get('classesText', '') or '', num(b.get('salary', 0) or 0, 'salary', 0), now())).lastrowid
    audit(c, u['username'], 'teacher_add', {'id': tid}); return {'id': tid}

def op_teacher_update(c, u, tid, b):
    r = c.execute('SELECT * FROM teachers WHERE id=?', (tid,)).fetchone()
    if not r: raise ApiError(404, 'NOT_FOUND')
    c.execute('UPDATE teachers SET name=?,subject=?,phone=?,classes_text=?,salary=?, salary_paid_at=? WHERE id=?',
              (b.get('name', r['name']), b.get('subject', r['subject']), b.get('phone', r['phone']), b.get('classesText', r['classes_text']),
               num(b['salary'], 'salary', 0) if 'salary' in b else r['salary'], None if 'salary' in b else r['salary_paid_at'], tid))
    audit(c, u['username'], 'teacher_update', {'id': tid}); return {'id': tid}

def op_teacher_delete(c, u, tid):
    if not c.execute('SELECT 1 FROM teachers WHERE id=?', (tid,)).fetchone(): raise ApiError(404, 'NOT_FOUND')
    c.execute('DELETE FROM teachers WHERE id=?', (tid,)); audit(c, u['username'], 'teacher_delete', {'id': tid}); return {'deleted': tid}

def op_teacher_pay_salary(c, u, tid):
    if not c.execute('SELECT 1 FROM teachers WHERE id=?', (tid,)).fetchone(): raise ApiError(404, 'NOT_FOUND')
    c.execute('UPDATE teachers SET salary_paid_at=? WHERE id=?', (now(), tid)); audit(c, u['username'], 'salary_paid', {'id': tid}); return {'id': tid}

def op_attendance(c, u, b):
    need(b, 'studentId', 'status'); d = vdate(b.get('date') or today(), 'date')
    if b['status'] not in ('present', 'absent', 'late'): raise ApiError(400, 'INVALID:status')
    s = c.execute('SELECT * FROM students WHERE id=?', (b['studentId'],)).fetchone()
    if not s: raise ApiError(404, 'STUDENT_NOT_FOUND')
    c.execute('INSERT INTO attendance(student_id,date,status,marked_by,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(student_id,date) DO UPDATE SET status=excluded.status,marked_by=excluded.marked_by,updated_at=excluded.updated_at',
              (s['id'], d, b['status'], u['username'], now()))
    key = f"ABSENCE|{s['id']}|{d}"
    if b['status'] == 'absent':
        msg = f"السلام عليكم ولي أمر الطالب {s['name']}، نحيطكم علمًا بأنه تم تسجيل غياب الطالب بتاريخ {d}."
        q = queue(c, 'ABSENCE_CREATED', key, student_guardian_phone(c, s['id']), msg, s['id'])
        if not q:  # ربما أُلغي سابقًا ثم عاد غائبًا: أعد تفعيله إن لم يُرسل
            c.execute("UPDATE notification_outbox SET status='pending',cancel_reason=NULL WHERE dedupe_key=? AND status='cancelled'", (key,))
    else:
        c.execute("UPDATE notification_outbox SET status='cancelled',cancel_reason='attendance_changed' WHERE dedupe_key=? AND status='pending'", (key,))
    audit(c, u['username'], 'attendance', {'student': s['id'], 'date': d, 'status': b['status']}); return {'studentId': s['id'], 'date': d, 'status': b['status']}

def op_installment_add(c, u, b):
    need(b, 'studentId', 'total', 'dueDate')
    s = c.execute('SELECT id FROM students WHERE id=? AND archived_at IS NULL', (b['studentId'],)).fetchone()
    if not s: raise ApiError(404, 'STUDENT_NOT_FOUND')
    total = num(b['total'], 'total'); 
    if total <= 0: raise ApiError(400, 'INVALID:total')
    disc = num(b.get('discount', 0) or 0, 'discount', 0)
    if disc >= total: raise ApiError(400, 'INVALID:discount')
    iv = int(num(b.get('intervalMonths', 0) or 0, 'intervalMonths', 0)); ar = 1 if (b.get('autoRenew') and iv > 0) else 0
    iid = c.execute('INSERT INTO installments(student_id,series_id,seq,total,discount,due_date,interval_months,auto_renew,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (s['id'], secrets.token_hex(6), 1, total, disc, vdate(b['dueDate'], 'dueDate'), iv, ar, now())).lastrowid
    audit(c, u['username'], 'installment_add', {'id': iid}); return {'id': iid}

def op_installment_discount(c, u, iid, b):
    i = c.execute('SELECT * FROM installments WHERE id=?', (iid,)).fetchone()
    if not i: raise ApiError(404, 'NOT_FOUND')
    disc = num(b.get('discount', 0), 'discount', 0)
    if disc > i['total']: raise ApiError(400, 'INVALID:discount')
    c.execute('UPDATE installments SET discount=? WHERE id=?', (disc, iid))
    i = c.execute('SELECT * FROM installments WHERE id=?', (iid,)).fetchone()
    renewed = None
    if inst_remaining(i) <= 0: cancel_overdue(c, iid, 'installment_paid'); c.execute('UPDATE installments SET closed_at=? WHERE id=?', (now(), iid)); renewed = renew_if_needed(c, iid, u['username'])
    audit(c, u['username'], 'installment_discount', {'id': iid, 'discount': disc}); return {'id': iid, 'renewed': renewed}

def op_installment_pay(c, u, iid, b):
    i = c.execute('SELECT * FROM installments WHERE id=?', (iid,)).fetchone()
    if not i: raise ApiError(404, 'NOT_FOUND')
    amt = num(b.get('amount'), 'amount')
    if amt <= 0: raise ApiError(400, 'INVALID:amount')
    rem = inst_remaining(i)
    if rem <= 0: raise ApiError(409, 'ALREADY_PAID')
    if amt > rem + 1e-9: raise ApiError(400, 'AMOUNT_EXCEEDS_REMAINING')
    c.execute('UPDATE installments SET paid=paid+? WHERE id=?', (amt, iid))
    c.execute('INSERT INTO payments(installment_id,student_id,amount,note,paid_at,by_user) VALUES(?,?,?,?,?,?)', (iid, i['student_id'], amt, b.get('note', '') or '', now(), u['username']))
    i = c.execute('SELECT * FROM installments WHERE id=?', (iid,)).fetchone(); renewed = None
    if inst_remaining(i) <= 1e-9:
        c.execute('UPDATE installments SET closed_at=? WHERE id=?', (now(), iid))
        cancel_overdue(c, iid, 'installment_paid')           # إيقاف إشعار التأخير فورًا وفي نفس المعاملة
        renewed = renew_if_needed(c, iid, u['username'])
    audit(c, u['username'], 'payment', {'installment': iid, 'amount': amt})
    return {'id': iid, 'remaining': inst_remaining(i), 'renewed': renewed}

def op_installment_delete(c, u, iid):
    if not c.execute('SELECT 1 FROM installments WHERE id=?', (iid,)).fetchone(): raise ApiError(404, 'NOT_FOUND')
    c.execute('DELETE FROM installments WHERE id=?', (iid,)); audit(c, u['username'], 'installment_delete', {'id': iid}); return {'deleted': iid}

def op_grade_add(c, u, b):
    need(b, 'studentId', 'subject', 'value')
    if not c.execute('SELECT 1 FROM students WHERE id=? AND archived_at IS NULL', (b['studentId'],)).fetchone(): raise ApiError(404, 'STUDENT_NOT_FOUND')
    v = num(b['value'], 'value')
    if not (0 <= v <= 100): raise ApiError(400, 'INVALID:value')
    gid = c.execute('INSERT INTO grades(student_id,subject,value,note,date,by_user,created_at) VALUES(?,?,?,?,?,?,?)',
                    (b['studentId'], b['subject'], v, b.get('note', '') or '', today(), u['username'], now())).lastrowid
    audit(c, u['username'], 'grade_add', {'id': gid}); return {'id': gid}

def op_grade_delete(c, u, gid):
    if not c.execute('SELECT 1 FROM grades WHERE id=?', (gid,)).fetchone(): raise ApiError(404, 'NOT_FOUND')
    c.execute('DELETE FROM grades WHERE id=?', (gid,)); audit(c, u['username'], 'grade_delete', {'id': gid}); return {'deleted': gid}

def tmin(t):
    """يحوّل نص وقت ('٨:٠٠ - ٨:٥٠' أو '08:30') إلى دقائق؛ أول وقت في النص. الساعات 1..6 تُعتبر مساءً."""
    t = str(t).translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
    m = re.search(r'(\d{1,2}):(\d{2})', t)
    if not m: return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59): return None
    return (h + 12 if 1 <= h <= 6 else h) * 60 + mi

def op_lesson_add(c, u, b):
    need(b, 'title', 'className', 'room', 'day', 'startTime', 'endTime')
    s0, e0 = tmin(b['startTime']), tmin(b['endTime'])
    if s0 is None or e0 is None or s0 >= e0: raise ApiError(400, 'INVALID:time')
    cid = ensure_class(c, b['className'])
    for r in c.execute('SELECT start_time,end_time FROM lessons WHERE class_id=? AND day=?', (cid, b['day'])):
        s1, e1 = tmin(r['start_time']), tmin(r['end_time'])
        if s1 is not None and e1 is not None and s1 < e0 and e1 > s0: raise ApiError(409, 'LESSON_CONFLICT')
    tr = c.execute('SELECT id FROM teachers WHERE name=?', (b.get('teacher', ''),)).fetchone()
    lid = c.execute('INSERT INTO lessons(subject,teacher_id,teacher_name,class_id,room,day,start_time,end_time,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (b['title'], tr['id'] if tr else None, b.get('teacher', '') or '', cid, b['room'], b['day'], b['startTime'], b['endTime'], now())).lastrowid
    audit(c, u['username'], 'lesson_add', {'id': lid}); return {'id': lid}

def op_lesson_delete(c, u, lid):
    if not c.execute('SELECT 1 FROM lessons WHERE id=?', (lid,)).fetchone(): raise ApiError(404, 'NOT_FOUND')
    c.execute('DELETE FROM lessons WHERE id=?', (lid,)); audit(c, u['username'], 'lesson_delete', {'id': lid}); return {'deleted': lid}

def op_settings(c, u, b):
    if b.get('schoolName'): c.execute('INSERT INTO settings VALUES("schoolName",?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (str(b['schoolName'])[:120],))
    audit(c, u['username'], 'settings', {}); return {}

def do_backup(tag='manual'):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    f = BACKUP_DIR / f"masar_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.db"
    src = conn(); dst = sqlite3.connect(f); src.backup(dst); dst.close(); src.close(); return f.name

def do_restore(name):
    f = (BACKUP_DIR / Path(name).name)
    if not f.exists(): raise ApiError(404, 'BACKUP_NOT_FOUND')
    probe = sqlite3.connect(f)
    if probe.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': probe.close(); raise ApiError(400, 'BACKUP_CORRUPT')
    probe.close(); do_backup('before_restore')
    src = sqlite3.connect(f); dst = conn(); src.backup(dst); dst.close(); src.close()

# ---------------------------------------------------------------- reports (من الجداول مباشرة)
def report(c, kind, role, q):
    if kind == 'students':
        return {'rows': [student_row(r) for r in c.execute(STUDENT_SQL + ' WHERE s.archived_at IS NULL ORDER BY s.name')]}
    if kind == 'attendance':
        y = q.get('year', [today()[:4]])[0]
        rows = [dict(r) for r in c.execute("SELECT a.date,s.id student_id,s.name student,a.status FROM attendance a JOIN students s ON s.id=a.student_id WHERE substr(a.date,1,4)=? ORDER BY a.date DESC,s.name", (y,))]
        summ = [dict(r) for r in c.execute("SELECT s.id student_id,s.name student,SUM(a.status='absent') absent,SUM(a.status='late') late,SUM(a.status='present') present FROM attendance a JOIN students s ON s.id=a.student_id WHERE substr(a.date,1,4)=? GROUP BY s.id ORDER BY s.name", (y,))]
        return {'year': y, 'rows': rows, 'summary': summ}
    if kind == 'finance':
        if not (can(role, 'finance') or can(role, 'reports_fin')): raise ApiError(403, 'FORBIDDEN')
        rows = []; t = today()
        for i in c.execute('SELECT i.*,s.name sname FROM installments i JOIN students s ON s.id=i.student_id ORDER BY i.due_date'):
            rem = inst_remaining(i); rows.append({'id': i['id'], 'student': i['sname'], 'total': i['total'], 'discount': i['discount'], 'paid': i['paid'], 'remaining': rem, 'dueDate': i['due_date'],
                                                  'status': 'paid' if rem <= 0 else ('overdue' if i['due_date'] < t else 'upcoming')})
        return {'rows': rows, 'totals': {'total': sum(r['total'] - r['discount'] for r in rows), 'paid': sum(r['paid'] for r in rows), 'remaining': sum(r['remaining'] for r in rows), 'overdue': sum(1 for r in rows if r['status'] == 'overdue')}}
    if kind == 'schedule':
        return {'rows': [dict(r) for r in c.execute('SELECT l.*,c.name class_name FROM lessons l JOIN classes c ON c.id=l.class_id ORDER BY c.id,l.day,l.start_time')]}
    if kind == 'grades':
        return {'rows': [dict(r) for r in c.execute('SELECT g.*,s.name student FROM grades g JOIN students s ON s.id=g.student_id ORDER BY s.name,g.subject')]}
    raise ApiError(404, 'NOT_FOUND')

# ---------------------------------------------------------------- WhatsApp Webhook (استقبال)
WA_VERIFY = lambda: os.environ.get('WHATSAPP_VERIFY_TOKEN', '')
WA_APP_SECRET = lambda: os.environ.get('WHATSAPP_APP_SECRET', '')

def wa_signature_ok(raw, header):
    sec = WA_APP_SECRET()
    if not sec or not header.startswith('sha256='): return False
    return hmac.compare_digest(hmac.new(sec.encode(), raw, hashlib.sha256).hexdigest(), header[7:])

def process_webhook(c, payload):
    """يحفظ الرسائل الواردة ويربطها بالطالب عبر رقم ولي الأمر، ويحدّث حالة التسليم للرسائل الصادرة."""
    got = upd = 0
    for e in payload.get('entry', []):
        for ch in e.get('changes', []):
            v = ch.get('value', {})
            names = {x.get('wa_id'): (x.get('profile') or {}).get('name', '') for x in v.get('contacts', [])}
            for m in v.get('messages', []):
                frm = re.sub(r'\D', '', str(m.get('from', ''))); mid = m.get('id')
                if not frm or not mid: continue
                body = (m.get('text') or {}).get('body') or (m.get('button') or {}).get('text') or ((m.get('interactive') or {}).get('button_reply') or {}).get('title') or f"[{m.get('type', 'unknown')}]"
                g = c.execute('SELECT id FROM guardians WHERE phone=?', (frm,)).fetchone()
                sid = None
                if g:
                    r = c.execute('SELECT id FROM students WHERE guardian_id=? AND archived_at IS NULL ORDER BY id LIMIT 1', (g['id'],)).fetchone(); sid = r['id'] if r else None
                try:
                    c.execute('INSERT INTO inbound_messages(wa_message_id,from_phone,student_id,profile_name,msg_type,body,received_at) VALUES(?,?,?,?,?,?,?)',
                              (mid, frm, sid, names.get(frm, ''), m.get('type', 'text'), str(body)[:2000], now())); got += 1
                except sqlite3.IntegrityError: pass          # تكرار تسليم من Meta
            for stt in v.get('statuses', []):
                mid, status = stt.get('id'), stt.get('status')
                if mid and status in ('delivered', 'read', 'failed'):
                    err = json.dumps(stt.get('errors', ''), ensure_ascii=False)[:500] if status == 'failed' else None
                    cur = c.execute('UPDATE notification_outbox SET provider_response=?, last_error=COALESCE(?,last_error) WHERE wa_message_id=?', (f'status:{status}', err, mid))
                    upd += cur.rowcount
    return got, upd

# ---------------------------------------------------------------- HTTP
def route_perm(method, parts):
    """(permission) المطلوبة لكل مسار — تُفرض على الخادم وليس الواجهة فقط."""
    a = parts[1] if len(parts) > 1 else ''
    w = method != 'GET'
    if a == 'students': return 'students' if w else 'students_read'
    if a == 'teachers': return 'teachers' if w else 'students_read'
    if a == 'attendance': return 'attendance' if w else 'students_read'
    if a in ('installments',): return 'finance'
    if a == 'grades': return 'grades' if w else 'students_read'
    if a == 'lessons': return 'lessons' if w else 'lessons_read'
    if a == 'users': return 'users'
    if a in ('backup', 'restore', 'backups'): return 'backup'
    if a == 'settings': return 'users'
    if a == 'scan': return 'outbox'
    if a == 'inbox': return 'outbox'
    return None

def perm_ok(role, perm, method):
    if perm is None: return True
    if can(role, perm): return True
    # القراءة مسموحة لمن يملك صلاحية الكتابة والعكس غير صحيح
    if perm == 'students_read': return any(can(role, p) for p in ('students', 'attendance', 'grades', 'finance', 'teachers', 'lessons'))
    if perm == 'lessons_read': return can(role, 'lessons') or can(role, 'lessons_read') or can(role, 'attendance')
    return False

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass
    def out(self, code, obj, ctype='application/json; charset=utf-8', raw=None):
        raw = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff'); self.end_headers(); self.wfile.write(raw)
    def body(self):
        n = int(self.headers.get('Content-Length', '0') or 0)
        if n > 2_000_000: raise ApiError(413, 'TOO_LARGE')
        try: return json.loads(self.rfile.read(n) or b'{}')
        except Exception: raise ApiError(400, 'BAD_JSON')
    def handle_any(self, method):
        try:
            path, _, qs = self.path.partition('?'); from urllib.parse import parse_qs; q = parse_qs(qs)
            if method == 'GET' and path in ('/', '/index.html', '/masar.html'):
                return self.out(200, None, 'text/html; charset=utf-8', (ROOT / 'masar.html').read_bytes())
            if not path.startswith('/api/'): return self.out(404, {'ok': False, 'error': 'NOT_FOUND'})
            if path == '/api/whatsapp/webhook':
                if method == 'GET':       # تحقق Meta عند تسجيل الـ Webhook
                    if q.get('hub.mode') == ['subscribe'] and WA_VERIFY() and q.get('hub.verify_token', [''])[0] == WA_VERIFY():
                        return self.out(200, None, 'text/plain', q.get('hub.challenge', [''])[0].encode())
                    return self.out(403, {'ok': False, 'error': 'VERIFY_FAILED'})
                if method == 'POST':
                    n = int(self.headers.get('Content-Length', '0') or 0)
                    if n > 2_000_000: return self.out(413, {'ok': False})
                    raw = self.rfile.read(n)
                    if not wa_signature_ok(raw, self.headers.get('X-Hub-Signature-256', '')): return self.out(403, {'ok': False, 'error': 'BAD_SIGNATURE'})
                    try: payload = json.loads(raw or b'{}')
                    except Exception: return self.out(400, {'ok': False, 'error': 'BAD_JSON'})
                    c = conn()
                    try:
                        with wlock:
                            c.execute('BEGIN IMMEDIATE'); r = process_webhook(c, payload); c.execute('COMMIT')
                    finally: c.close()
                    return self.out(200, {'ok': True, 'received': r[0], 'statusUpdates': r[1]})
                return self.out(405, {'ok': False})
            parts = path.strip('/').split('/'); a = parts[1] if len(parts) > 1 else ''
            body = self.body() if method in ('POST', 'PUT', 'DELETE') else {}
            check_text(body)
            c = conn()
            try:
                if a == 'health' and method == 'GET':
                    n = c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']
                    return self.out(200, {'ok': True, 'database': 'sqlite', 'signupEnabled': bool(ACCESS_CODE()), 'whatsappConfigured': wa_configured(), 'webhookConfigured': bool(WA_VERIFY() and WA_APP_SECRET())})
                if a == 'register' and method == 'POST':
                    code = ACCESS_CODE()
                    if not code: raise ApiError(403, 'SIGNUP_DISABLED')
                    throttle_check(self.client_address[0])
                    if not hmac.compare_digest(str(body.get('accessCode', '')).encode(), code.encode()):
                        throttle_fail(self.client_address[0]); raise ApiError(403, 'BAD_ACCESS_CODE')
                    with wlock:
                        c.execute('BEGIN IMMEDIATE')
                        first = c.execute('SELECT COUNT(*) n FROM users').fetchone()['n'] == 0
                        try: create_user(c, body.get('username', ''), body.get('password', ''), 'admin', body.get('fullName', ''))
                        except BaseException: c.execute('ROLLBACK'); raise
                        if first and body.get('schoolName'): c.execute('INSERT OR REPLACE INTO settings VALUES("schoolName",?)', (str(body['schoolName'])[:120],))
                        c.execute('COMMIT')
                    throttle_ok(self.client_address[0])
                    return self.out(200, {'ok': True})
                if a == 'login' and method == 'POST':
                    throttle_check(self.client_address[0])
                    r = login(c, body.get('username', ''), body.get('password', ''))
                    if not r: throttle_fail(self.client_address[0]); raise ApiError(401, 'BAD_CREDENTIALS')
                    throttle_ok(self.client_address[0])
                    return self.out(200, {'ok': True, 'token': r[0], 'user': {'username': r[1]['username'], 'role': r[1]['role'], 'fullName': r[1]['full_name']}})
                tok = (self.headers.get('Authorization', '')[7:] if self.headers.get('Authorization', '').startswith('Bearer ') else '')
                u = user_from_token(c, tok)
                if not u: raise ApiError(401, 'UNAUTHORIZED')
                if a == 'logout': c.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(tok.encode()).hexdigest(),)); return self.out(200, {'ok': True})
                if a == 'me': return self.out(200, {'ok': True, 'user': {'username': u['username'], 'role': u['role'], 'fullName': u['full_name']}})
                if not perm_ok(u['role'], route_perm(method, parts), method): raise ApiError(403, 'FORBIDDEN')
                res = self.dispatch(c, u, method, parts, body, q)
                return self.out(200, {'ok': True, **(res or {})})
            finally: c.close()
        except ApiError as e: self.out(e.code, {'ok': False, 'error': e.msg})
        except sqlite3.IntegrityError as e: self.out(409, {'ok': False, 'error': 'INTEGRITY:' + str(e)})
        except Exception as e:
            import traceback; traceback.print_exc(); self.out(500, {'ok': False, 'error': 'SERVER_ERROR'})

    def tx(self, c, fn, *args):
        with wlock:
            c.execute('BEGIN IMMEDIATE')
            try: r = fn(c, *args); c.execute('COMMIT'); return r
            except BaseException: c.execute('ROLLBACK'); raise

    def dispatch(self, c, u, m, p, b, q):
        a = p[1]; rid = int(p[2]) if len(p) > 2 and p[2].isdigit() else None; sub = p[3] if len(p) > 3 else ''
        role = u['role']
        if a == 'state' and m == 'GET': return {'state': build_state(c, role)}
        if a == 'students':
            if m == 'POST': return self.tx(c, op_student_add, u, b)
            if m == 'PUT' and rid: return self.tx(c, op_student_update, u, rid, b)
            if m == 'DELETE' and rid:
                if q.get('hard') == ['1']:
                    if not can(role, 'delete'): raise ApiError(403, 'FORBIDDEN')
                    return self.tx(c, op_student_delete, u, rid)
                if not can(role, 'students'): raise ApiError(403, 'FORBIDDEN')
                return self.tx(c, op_student_archive, u, rid)
        if a == 'teachers':
            if m == 'POST' and rid and sub == 'pay-salary': return self.tx(c, op_teacher_pay_salary, u, rid)
            if m == 'POST': return self.tx(c, op_teacher_add, u, b)
            if m == 'PUT' and rid: return self.tx(c, op_teacher_update, u, rid, b)
            if m == 'DELETE' and rid:
                if not can(role, 'delete'): raise ApiError(403, 'FORBIDDEN')
                return self.tx(c, op_teacher_delete, u, rid)
        if a == 'attendance' and m == 'POST':
            if p[-1] == 'bulk':
                def bulk(c2, u2, items): return {'saved': [op_attendance(c2, u2, i) for i in items]}
                return self.tx(c, bulk, u, b.get('items', []))
            return self.tx(c, op_attendance, u, b)
        if a == 'installments':
            if m == 'POST' and rid and sub == 'pay': return self.tx(c, op_installment_pay, u, rid, b)
            if m == 'POST': return self.tx(c, op_installment_add, u, b)
            if m == 'PUT' and rid: return self.tx(c, op_installment_discount, u, rid, b)
            if m == 'DELETE' and rid:
                if not can(role, 'delete'): raise ApiError(403, 'FORBIDDEN')
                return self.tx(c, op_installment_delete, u, rid)
        if a == 'grades':
            if m == 'POST': return self.tx(c, op_grade_add, u, b)
            if m == 'DELETE' and rid:
                if not can(role, 'delete'): raise ApiError(403, 'FORBIDDEN')
                return self.tx(c, op_grade_delete, u, rid)
        if a == 'lessons':
            if m == 'POST': return self.tx(c, op_lesson_add, u, b)
            if m == 'DELETE' and rid: return self.tx(c, op_lesson_delete, u, rid)
        if a == 'reports' and m == 'GET':
            kind = p[2] if len(p) > 2 else ''
            return report(c, kind, role, q)
        if a == 'users':
            if m == 'GET': return {'users': [{'id': r['id'], 'username': r['username'], 'role': r['role'], 'fullName': r['full_name'], 'active': bool(r['active'])} for r in c.execute('SELECT * FROM users')]}
            if m == 'POST': return self.tx(c, lambda c2, u2, b2: {'id': create_user(c2, b2.get('username', ''), b2.get('password', ''), b2.get('role', ''), b2.get('fullName', ''))}, u, b)
            if m == 'DELETE' and rid:
                if rid == u['id']: raise ApiError(400, 'CANNOT_DELETE_SELF')
                return self.tx(c, lambda c2, u2, i: (c2.execute('UPDATE users SET active=0 WHERE id=?', (i,)), c2.execute('DELETE FROM sessions WHERE user_id=?', (i,)), {'deactivated': i})[2], u, rid)
        if a == 'inbox' and m == 'GET':
            return {'messages': [dict(r) for r in c.execute('SELECT i.*, s.name student FROM inbound_messages i LEFT JOIN students s ON s.id=i.student_id ORDER BY i.id DESC LIMIT 200')]}
        if a == 'settings' and m == 'PUT': return self.tx(c, op_settings, u, b)
        if a == 'scan' and m == 'POST':
            def sc(c2, u2): return {'queued': scan_overdue(c2)}
            return self.tx(c, sc, u)
        if a == 'backup' and m == 'POST': return {'file': do_backup()}
        if a == 'backups' and m == 'GET': return {'files': sorted(x.name for x in BACKUP_DIR.glob('*.db'))} if BACKUP_DIR.exists() else {'files': []}
        if a == 'restore' and m == 'POST':
            with wlock: do_restore(b.get('file', ''))
            return {'restored': b.get('file')}
        raise ApiError(404, 'NOT_FOUND')

    def do_GET(self): self.handle_any('GET')
    def do_POST(self): self.handle_any('POST')
    def do_PUT(self): self.handle_any('PUT')
    def do_DELETE(self): self.handle_any('DELETE')

def backup_loop():
    last = None
    while True:
        d = today()
        if d != last:
            try:
                if DB_PATH.exists(): do_backup('daily')
                files = sorted(BACKUP_DIR.glob('*_daily.db'))
                for f in files[:-14]: f.unlink()
            except Exception as e: print('backup error', e, file=sys.stderr)
            last = d
        time.sleep(3600)

def main():
    migrate()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=backup_loop, daemon=True).start()
    print(f'MASAR http://{HOST}:{PORT}  db={DB_PATH}  whatsapp={"configured" if wa_configured() else "NOT configured"}', flush=True)
    class Srv(ThreadingHTTPServer):
        request_queue_size = 256      # الافتراضي 5 يسبب Connection reset عند ضغط متزامن
        daemon_threads = True
    Srv((HOST, PORT), Handler).serve_forever()

if __name__ == '__main__': main()
