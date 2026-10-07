# -*- coding: utf-8 -*-
"""استقبال واتساب (Webhook) — يحاكي طلبات Meta الموقّعة محليًا. ليس اختبارًا مع Meta الحقيقية."""
import os, sys, json, time, hmac, hashlib, subprocess, tempfile, urllib.request, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); T = tempfile.mkdtemp(); PORT = 18980; B = f'http://127.0.0.1:{PORT}'
RES = []; tok = {}
def check(n, c, e=''):
    RES.append((n, bool(c))); print(('PASS ' if c else 'FAIL ') + n + (f'  [{e}]' if e and not c else ''), flush=True)
def raw(m, path, data=None, headers=None):
    r = urllib.request.Request(B + path, data=data, method=m, headers=headers or {})
    try:
        with urllib.request.urlopen(r) as x: return x.status, x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()
def call(m, path, body=None, who='admin'):
    s, t = raw(m, path, json.dumps(body).encode() if body is not None else None, {'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + tok[who]} if who in tok else {})}); return s, json.loads(t)
SECRET = 's3cret-app-secret'
env = {k: v for k, v in os.environ.items() if not k.startswith('WHATSAPP_')}
env.update(MASAR_DB=T + '/d.db', MASAR_BACKUPS=T + '/b', MASAR_PORT=str(PORT), MASAR_HOST='127.0.0.1', MASAR_ACCESS_CODE='CODE-123', WHATSAPP_VERIFY_TOKEN='verify-me', WHATSAPP_APP_SECRET=SECRET)
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, 'server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(B + '/api/health'); break
    except Exception: time.sleep(0.2)
def signed(payload, secret=SECRET):
    b = json.dumps(payload).encode(); return b, {'Content-Type': 'application/json', 'X-Hub-Signature-256': 'sha256=' + hmac.new(secret.encode(), b, hashlib.sha256).hexdigest()}
try:
    call('POST', '/api/register', {'username': 'admin', 'password': 'Admin#12345', 'accessCode': 'CODE-123'}); tok['admin'] = call('POST', '/api/login', {'username': 'admin', 'password': 'Admin#12345'})[1]['token']
    call('POST', '/api/users', {'username': 't', 'password': 'Passw0rd!!', 'role': 'teacher'}); tok['teacher'] = call('POST', '/api/login', {'username': 't', 'password': 'Passw0rd!!'})[1]['token']
    sid = call('POST', '/api/students', {'name': 'اختبار مِسار 001', 'className': 'السادس أ', 'guardianPhone': '0926853251'})[1]['id']
    s, t = raw('GET', '/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=12345')
    check('W01 تحقق Meta (verify token صحيح) يعيد challenge', s == 200 and t == '12345', (s, t))
    check('W02 verify token خاطئ مرفوض', raw('GET', '/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=bad&hub.challenge=1')[0] == 403)
    msg = {'entry': [{'changes': [{'value': {'contacts': [{'wa_id': '218926853251', 'profile': {'name': 'أبو الطالب'}}], 'messages': [{'from': '218926853251', 'id': 'wamid.IN1', 'type': 'text', 'text': {'body': 'السلام عليكم، سأحضر غدًا لسداد القسط'}}]}}]}]}
    b, h = signed(msg); s, t = raw('POST', '/api/whatsapp/webhook', b, h)
    check('W03 رسالة واردة موقّعة تُقبل وتُحفظ', s == 200 and json.loads(t)['received'] == 1, (s, t))
    b2, h2 = signed(msg, 'wrong'); check('W04 توقيع مزوّر مرفوض', raw('POST', '/api/whatsapp/webhook', b2, h2)[0] == 403)
    check('W05 بدون توقيع مرفوض', raw('POST', '/api/whatsapp/webhook', b, {'Content-Type': 'application/json'})[0] == 403)
    s, t = raw('POST', '/api/whatsapp/webhook', b, h); check('W06 إعادة تسليم نفس الرسالة لا تتكرر', json.loads(t)['received'] == 0)
    inbox = call('GET', '/api/inbox')[1]['messages']
    check('W07 الرسالة مرتبطة بالطالب عبر رقم ولي الأمر', len(inbox) == 1 and inbox[0]['student'] == 'اختبار مِسار 001' and 'القسط' in inbox[0]['body'] and inbox[0]['profile_name'] == 'أبو الطالب')
    check('W08 المعلم لا يرى صندوق الوارد (403)', call('GET', '/api/inbox', None, 'teacher')[0] == 403)
    unk = {'entry': [{'changes': [{'value': {'messages': [{'from': '218911999999', 'id': 'wamid.IN2', 'type': 'image'}]}}]}]}
    b3, h3 = signed(unk); raw('POST', '/api/whatsapp/webhook', b3, h3)
    check('W09 رقم غير معروف يُحفظ بدون ربط ونوع غير نصي لا يكسر', len(call('GET', '/api/inbox')[1]['messages']) == 2)
    check('W10 health يعرض webhookConfigured', call('GET', '/api/health')[1]['webhookConfigured'] is True)
finally: srv.terminate()
f = [n for n, ok in RES if not ok]; print(f'\nTOTAL {len(RES)}  PASS {len(RES) - len(f)}  FAIL {len(f)}'); sys.exit(1 if f else 0)
