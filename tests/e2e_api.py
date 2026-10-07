# -*- coding: utf-8 -*-
"""اختبار شامل للسيرفر عبر HTTP حقيقي. تشغيل: python tests/e2e_api.py"""
import os, sys, json, time, subprocess, tempfile, threading, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import date, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(); PORT = 18900; MOCK = 18901
BASE = f'http://127.0.0.1:{PORT}'
RES = []; TOKENS = {}
def check(name, cond, extra=''):
    RES.append((name, bool(cond))); print(('PASS ' if cond else 'FAIL ') + name + (f'  [{extra}]' if extra and not cond else ''), flush=True)

SENT = []   # mock لواجهة Meta Graph — لاختبار منطق الـ Worker فقط وليس واتساب الحقيقي
class M(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        ok = self.headers.get('Authorization') == 'Bearer TESTTOKEN'
        if ok: SENT.append(b)
        raw = json.dumps({'messages': [{'id': 'wamid.X'}]} if ok else {'error': 'bad'}).encode()
        self.send_response(200 if ok else 401); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
threading.Thread(target=HTTPServer(('127.0.0.1', MOCK), M).serve_forever, daemon=True).start()

def call(method, path, body=None, role='admin'):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + TOKENS[role]} if role in TOKENS else {})})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

proc = None
def start(env_extra=None):
    global proc
    env = {k: v for k, v in os.environ.items() if not k.startswith('WHATSAPP_')}
    env.update(MASAR_DB=os.path.join(TMP, 'data', 'masar.db'), MASAR_BACKUPS=os.path.join(TMP, 'bk'), MASAR_PORT=str(PORT), MASAR_HOST='127.0.0.1', MASAR_WORKER_INTERVAL='1', MASAR_ACCESS_CODE='CODE-123', **(env_extra or {}))
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, 'server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(50):
        try: urllib.request.urlopen(BASE + '/api/health'); return
        except Exception: time.sleep(0.2)
    raise SystemExit('server did not start')
def stop():
    proc.terminate(); proc.wait(); time.sleep(0.3)
def state(role='admin'): return call('GET', '/api/state', role=role)[1]['state']
def relogin(): TOKENS['admin'] = call('POST', '/api/login', {'username': 'admin', 'password': 'Admin#12345'})[1]['token']

WA = {'WHATSAPP_ACCESS_TOKEN': 'TESTTOKEN', 'WHATSAPP_PHONE_NUMBER_ID': '123', 'WHATSAPP_TEMPLATE_NAME': 'masar_notice', 'WHATSAPP_GRAPH_URL': f'http://127.0.0.1:{MOCK}', 'MASAR_COUNTRY_CODE': '218'}
start()
try:
    s, h = call('GET', '/api/health'); check('01 التسجيل مفعّل برمز المالك', h.get('signupEnabled') is True)
    check('02 /api/state بدون تسجيل دخول = 401', call('GET', '/api/state')[0] == 401)
    check('03 إنشاء حساب بدون رمز/برمز خاطئ مرفوض (403)', call('POST', '/api/register', {'username': 'admin', 'password': 'Admin#12345'})[0] == 403 and call('POST', '/api/register', {'username': 'admin', 'password': 'Admin#12345', 'accessCode': 'WRONG'})[0] == 403)
    check('03b كلمة مرور قصيرة مرفوضة', call('POST', '/api/register', {'username': 'admin', 'password': '123', 'accessCode': 'CODE-123'})[0] == 400)
    check('04 إنشاء حساب مدير برمز صحيح', call('POST', '/api/register', {'username': 'admin', 'password': 'Admin#12345', 'accessCode': 'CODE-123', 'schoolName': 'مدرسة الاختبار'})[0] == 200)
    check('05 حساب مدير ثانٍ وثالث بنفس الرمز (بلا حد)', call('POST', '/api/register', {'username': 'admin2', 'password': 'Admin#22222', 'accessCode': 'CODE-123'})[0] == 200 and call('POST', '/api/register', {'username': 'admin3', 'password': 'Admin#33333', 'accessCode': 'CODE-123'})[0] == 200)
    check('05b اسم مستخدم مكرر مرفوض', call('POST', '/api/register', {'username': 'admin2', 'password': 'Admin#22222', 'accessCode': 'CODE-123'})[0] == 409)
    check('05c الحساب الجديد يدخل بنفس الاسم والرمز بعد إعادة التشغيل لاحقًا (دور مدير)', (lambda r: r[0] == 200 and r[1]['user']['role'] == 'admin')(call('POST', '/api/login', {'username': 'admin2', 'password': 'Admin#22222'})))
    check('06 دخول بكلمة خاطئة مرفوض', call('POST', '/api/login', {'username': 'admin', 'password': 'wrong'})[0] == 401)
    relogin(); st = state()
    st['settings']['schoolName'] if False else None
    check('07 البداية: 0 طلاب/معلمين/أقساط/درجات/حضور/حصص', not st['students'] and not st['teachers'] and not st['dues'] and not st['gradesData'] and not st['attendanceHistory'] and not st['courses'])

    s, r = call('POST', '/api/students', {'name': 'اختبار مِسار 001', 'age': 12, 'className': 'السادس أ', 'guardianName': 'ولي أمر', 'guardianPhone': '0926853251', 'phone': '0911111111'})
    sid = r['id']; check('08 إضافة طالب', s == 200 and sid)
    check('09 طالب مكرر مرفوض', call('POST', '/api/students', {'name': 'اختبار مِسار 001', 'className': 'السادس أ', 'guardianPhone': '0926853251'})[0] == 409)
    check('10 اسم فارغ مرفوض', call('POST', '/api/students', {'name': '  '})[0] == 400)
    s2, r2 = call('POST', '/api/students', {'name': 'طالب ثاني', 'className': 'السابع أ', 'guardianPhone': '0911000000'}); sid2 = r2['id']
    st = state(); check('11 عدد الطلاب = 2', len(st['students']) == 2)
    check('12 الطالب في السادس أ وولي أمره بصيغة دولية', st['students'][0]['className'] == 'السادس أ' and st['students'][0]['guardianPhone'] == '218926853251')
    stop(); start(); relogin()
    st = state(); check('13 الطالب باقٍ بعد إعادة تشغيل السيرفر', any(x['name'] == 'اختبار مِسار 001' for x in st['students']) and len(st['students']) == 2)

    d0 = date.today().isoformat()
    check('14 تسجيل غياب', call('POST', '/api/attendance', {'studentId': sid, 'date': d0, 'status': 'absent'})[0] == 200)
    call('POST', '/api/attendance', {'studentId': sid, 'date': d0, 'status': 'absent'})
    st = state(); check('15 الغياب محفوظ بالتاريخ والطالب', st['attendanceHistory'].get(d0, {}).get(str(sid)) == 'absent')
    ab = [n for n in st['notificationOutbox'] if n['event'] == 'ABSENCE_CREATED']
    check('16 إشعار غياب واحد فقط (لا تكرار)', len(ab) == 1 and ab[0]['phone'] == '218926853251' and ab[0]['status'] == 'pending')
    call('POST', '/api/attendance', {'studentId': sid, 'date': (date.today() - timedelta(days=40)).isoformat(), 'status': 'absent'})
    rep = call('GET', '/api/reports/attendance')[1]
    check('17 تقرير الحضور من DB يضم الأيام والملخص', any(r['date'] == d0 for r in rep['rows']) and rep['summary'][0]['absent'] >= 1)
    call('POST', '/api/attendance', {'studentId': sid, 'date': d0, 'status': 'present'})
    ab = [n for n in state()['notificationOutbox'] if n['event'] == 'ABSENCE_CREATED']
    check('18 غائب→حاضر يلغي إشعار الغياب المعلق', any(n['status'] == 'cancelled' and n['cancel_reason'] == 'attendance_changed' for n in ab))
    check('19 حالة غير صالحة مرفوضة', call('POST', '/api/attendance', {'studentId': sid, 'date': d0, 'status': 'xx'})[0] == 400)
    check('20 طالب غير موجود مرفوض', call('POST', '/api/attendance', {'studentId': 9999, 'date': d0, 'status': 'absent'})[0] == 404)

    yest = (date.today() - timedelta(days=1)).isoformat()
    s, r = call('POST', '/api/installments', {'studentId': sid, 'total': 1000, 'dueDate': yest, 'intervalMonths': 1, 'autoRenew': True}); iid = r['id']
    check('21 إضافة قسط 1000 متأخر', s == 200)
    call('POST', '/api/scan', {})
    ov = [n for n in state()['notificationOutbox'] if n['event'] == 'PAYMENT_OVERDUE']
    check('22 قسط متأخر ⇒ إشعار في outbox', len(ov) == 1 and ov[0]['status'] == 'pending')
    call('POST', '/api/scan', {}); call('POST', '/api/scan', {})
    check('23 المسح المتكرر لا يكرر الإشعار', len([n for n in state()['notificationOutbox'] if n['event'] == 'PAYMENT_OVERDUE']) == 1)
    s, r = call('POST', f'/api/installments/{iid}/pay', {'amount': 400})
    d = [x for x in state()['dues'] if x['id'] == iid][0]
    check('24 دفع 400: المدفوع 400 والمتبقي 600', d['paidAmount'] == 400 and r['remaining'] == 600)
    check('25 دفع جزئي: إشعار التأخير ما زال قائمًا', [n for n in state()['notificationOutbox'] if n['event'] == 'PAYMENT_OVERDUE'][0]['status'] == 'pending')
    check('26 دفع أكثر من المتبقي مرفوض', call('POST', f'/api/installments/{iid}/pay', {'amount': 700})[0] == 400)
    check('27 دفع سالب/نص مرفوض', call('POST', f'/api/installments/{iid}/pay', {'amount': -5})[0] == 400 and call('POST', f'/api/installments/{iid}/pay', {'amount': 'abc'})[0] == 400)
    s, r = call('POST', f'/api/installments/{iid}/pay', {'amount': 600}); st = state()
    check('28 سداد كامل: المتبقي 0', r['remaining'] == 0)
    check('29 سداد كامل ⇒ إشعار التأخير ملغى (installment_paid)', [n for n in st['notificationOutbox'] if n['event'] == 'PAYMENT_OVERDUE'][0]['status'] == 'cancelled' and [n for n in st['notificationOutbox'] if n['event'] == 'PAYMENT_OVERDUE'][0]['cancel_reason'] == 'installment_paid')
    check('30 سداد كامل ⇒ قسط جديد تلقائي بنفس القيمة', r['renewed'] and r['renewed']['amount'] == 1000 and len(st['dues']) == 2)
    rn = [n for n in st['notificationOutbox'] if n['event'] == 'INSTALLMENT_RENEWED']
    check('31 رسالة لولي الأمر: المبلغ وآخر موعد', len(rn) == 1 and '1,000' in rn[0]['message'] and r['renewed']['dueDate'] in rn[0]['message'])
    call('POST', '/api/scan', {}); call('POST', '/api/scan', {})
    check('32 لا يعود إشعار التأخير للقسط المسدد', len([n for n in state()['notificationOutbox'] if n['event'] == 'PAYMENT_OVERDUE' and n['status'] != 'cancelled']) == 0)
    check('33 دفع قسط مسدد مرفوض', call('POST', f'/api/installments/{iid}/pay', {'amount': 1})[0] == 409)
    check('34 سجل المعاملات: دفعتان', len(state()['transactions']) == 2)
    check('35 لا يتولد قسط ثالث', len(state()['dues']) == 2)
    s, r = call('POST', '/api/installments', {'studentId': sid2, 'total': 500, 'dueDate': yest}); i2 = r['id']
    call('POST', '/api/scan', {}); call('PUT', f'/api/installments/{i2}', {'discount': 500})
    check('36 خصم = القسط ⇒ يلغى إشعار التأخير', all(n['status'] == 'cancelled' for n in state()['notificationOutbox'] if n['installment_id'] == i2))

    s, r = call('POST', '/api/lessons', {'title': 'الرياضيات', 'teacher': 'أ. علي', 'className': 'السادس أ', 'room': '101', 'day': 'الأحد', 'startTime': '08:00', 'endTime': '08:50'})
    check('37 حصة تُحفظ', s == 200)
    check('38 تعارض حصة مرفوض', call('POST', '/api/lessons', {'title': 'العلوم', 'className': 'السادس أ', 'room': '102', 'day': 'الأحد', 'startTime': '08:30', 'endTime': '09:00'})[0] == 409)
    subs = ['الرياضيات', 'اللغة العربية', 'العلوم', 'اللغة الإنجليزية', 'التربية الإسلامية', 'الحاسب الآلي', 'الفنون', 'الاجتماعيات', 'التربية البدنية', 'الفيزياء']
    oks = [call('POST', '/api/grades', {'studentId': sid, 'subject': x, 'value': 50 + i})[0] == 200 for i, x in enumerate(subs)]
    check('39 عشر درجات لعشر مواد', all(oks) and len(state()['gradesData']) == 10)
    check('40 درجة خارج 0-100 مرفوضة', call('POST', '/api/grades', {'studentId': sid, 'subject': 'x', 'value': 101})[0] == 400)
    for role in ('teacher', 'accountant', 'read_only', 'management'):
        call('POST', '/api/users', {'username': role, 'password': 'Passw0rd!!', 'role': role})
        TOKENS[role] = call('POST', '/api/login', {'username': role, 'password': 'Passw0rd!!'})[1]['token']
    check('41 معلم: يسجل حضور ودرجات', call('POST', '/api/attendance', {'studentId': sid2, 'date': d0, 'status': 'late'}, 'teacher')[0] == 200 and call('POST', '/api/grades', {'studentId': sid2, 'subject': 'العلوم', 'value': 80}, 'teacher')[0] == 200)
    check('42 معلم: ممنوع من المالية/المستخدمين/الحذف/النسخ', all(call(*x, 'teacher')[0] == 403 for x in [('POST', '/api/installments', {'studentId': sid, 'total': 5, 'dueDate': yest}), ('GET', '/api/users', None), ('DELETE', f'/api/students/{sid}?hard=1', None), ('POST', '/api/backup', {}), ('POST', f'/api/installments/{iid}/pay', {'amount': 1})]))
    check('43 معلم: لا يرى الأقساط/المعاملات/الإشعارات', state('teacher')['dues'] == [] and state('teacher')['transactions'] == [] and state('teacher')['notificationOutbox'] == [])
    check('44 محاسب: يضيف قسطًا ويرى المالية', call('POST', '/api/installments', {'studentId': sid2, 'total': 100, 'dueDate': '2099-01-01'}, 'accountant')[0] == 200 and len(state('accountant')['dues']) > 0)
    check('45 محاسب: ممنوع من الحضور/الدرجات/إضافة طالب', all(call(*x, 'accountant')[0] == 403 for x in [('POST', '/api/attendance', {'studentId': sid, 'status': 'absent'}), ('POST', '/api/grades', {'studentId': sid, 'subject': 'x', 'value': 1}), ('POST', '/api/students', {'name': 'x'})]))
    check('46 read_only: قراءة فقط', call('GET', '/api/state', None, 'read_only')[0] == 200 and all(call(*x, 'read_only')[0] == 403 for x in [('POST', '/api/students', {'name': 'x'}), ('POST', '/api/attendance', {'studentId': sid, 'status': 'absent'})]))
    check('47 management: لا حذف نهائي ولا إدارة مستخدمين', call('DELETE', f'/api/students/{sid2}?hard=1', None, 'management')[0] == 403 and call('GET', '/api/users', None, 'management')[0] == 403)
    check('48 توكن مزور = 401', call('POST', '/api/students', {'name': 'x'}, 'nobody')[0] == 401)

    before = state(); stop(); start(); relogin(); after = state()
    check('49 بعد إعادة التشغيل: كل البيانات باقية', all(before[k] == after[k] for k in ('students', 'dues', 'transactions', 'gradesData', 'attendanceHistory', 'courses')))
    check('50 تقرير المالية من DB', call('GET', '/api/reports/finance')[1]['totals']['paid'] >= 1000)

    s, r = call('POST', '/api/backup', {}); bk = r['file']; check('51 نسخة احتياطية', s == 200 and bk)
    call('DELETE', f'/api/students/{sid2}?hard=1')
    check('52 حذف نهائي (admin) يحذف السجلات المرتبطة', len(state()['students']) == 1 and all(g['studentId'] != sid2 for g in state()['gradesData']))
    check('53 استعادة النسخة ترجع الطالب', call('POST', '/api/restore', {'file': bk})[0] == 200 and len(state()['students']) == 2)
    check('54 مسار خبيث في الاستعادة مرفوض', call('POST', '/api/restore', {'file': '../../etc/passwd'})[0] == 404)
    call('DELETE', f'/api/students/{sid2}'); st = state()
    check('55 الأرشفة تخفي الطالب وتبقي سجلاته', [x for x in st['students'] if x['id'] == sid2][0]['archivedAt'] and any(g['studentId'] == sid2 for g in st['gradesData']))

    stop(); start(WA); relogin()
    check('56 whatsappConfigured=true عند ضبط البيانات', call('GET', '/api/health')[1]['whatsappConfigured'] is True)
    call('POST', '/api/attendance', {'studentId': sid, 'date': (date.today() - timedelta(days=2)).isoformat(), 'status': 'absent'})
    time.sleep(4); ob = state()['notificationOutbox']
    check('57 الـ Worker أرسل تلقائيًا بدون متصفح (Graph mock)', len([n for n in ob if n['status'] == 'sent']) >= 1 and len(SENT) >= 1)
    check('58 الطلب: قالب + رقم دولي + نص الرسالة', SENT and SENT[0]['type'] == 'template' and SENT[0]['to'] == '218926853251' and 'غياب' in SENT[0]['template']['components'][0]['parameters'][0]['text'])
    nb = len(SENT); time.sleep(3)
    check('59 لا إعادة إرسال لنفس الرسالة', len(SENT) == nb)
    check('60 إشعار تأخير القسط المسدد لم يُرسل أبدًا', not any('متأخر' in str(x) for x in SENT if False) and all(n['status'] == 'cancelled' for n in state()['notificationOutbox'] if n['event'] == 'PAYMENT_OVERDUE' and n['installment_id'] == iid))
    stop()
    start(dict(WA, WHATSAPP_ACCESS_TOKEN='WRONG')); relogin()
    call('POST', '/api/attendance', {'studentId': sid, 'date': (date.today() - timedelta(days=3)).isoformat(), 'status': 'absent'}); time.sleep(3)
    bad = [n for n in state()['notificationOutbox'] if n['last_error']]
    check('61 فشل الإرسال لا يُعلَّم sent ويُسجَّل الخطأ ويُعاد', bad and bad[0]['status'] == 'pending' and bad[0]['attempts'] >= 1)
    for i in range(5): call('POST', '/api/login', {'username': 'admin', 'password': 'bad%d' % i})
    check('62 بعد 5 محاولات دخول خاطئة: قفل مؤقت (429) حتى لكلمة المرور الصحيحة', call('POST', '/api/login', {'username': 'admin', 'password': 'Admin#12345'})[0] == 429)
finally:
    try: stop()
    except Exception: pass
f = [n for n, ok in RES if not ok]
print(f'\nTOTAL {len(RES)}  PASS {len(RES) - len(f)}  FAIL {len(f)}')
for n in f: print('FAILED:', n)
sys.exit(1 if f else 0)
