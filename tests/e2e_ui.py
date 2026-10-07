# -*- coding: utf-8 -*-
"""اختبار الواجهة في Chromium حقيقي مقابل السيرفر الحقيقي. تشغيل: python tests/e2e_ui.py"""
import subprocess, sys, os, time, tempfile, json, urllib.request
from datetime import date, timedelta
from playwright.sync_api import sync_playwright
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(); PORT = 18950; URL = f'http://127.0.0.1:{PORT}/'
RES = []
def check(n, c, extra=''):
    RES.append((n, bool(c))); print(('PASS ' if c else 'FAIL ') + n + (f'  [{extra}]' if extra and not c else ''), flush=True)
env = {k: v for k, v in os.environ.items() if not k.startswith('WHATSAPP_')}
env.update(MASAR_DB=T + '/d.db', MASAR_BACKUPS=T + '/b', MASAR_PORT=str(PORT), MASAR_HOST='127.0.0.1', MASAR_WORKER_INTERVAL='1', MASAR_ACCESS_CODE='CODE-123')
srv = None
def start():
    global srv
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, 'server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try: urllib.request.urlopen(URL + 'api/health'); return
        except Exception: time.sleep(0.2)
def stop(): srv.terminate(); srv.wait(); time.sleep(0.3)
start()
yest = (date.today() - timedelta(days=1)).isoformat()
try:
  with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={'width': 1300, 'height': 900})
    errs = []; pg.on('pageerror', lambda e: errs.append(str(e) + ' @ ' + str(getattr(e, 'stack', ''))[:300])); pg.on('console', lambda m: errs.append('console: ' + m.text[:300]) if m.type == 'error' and 'Failed to load resource' not in m.text else None)
    prompts = []
    def on_dialog(d):
        if d.type == 'prompt': d.accept(prompts.pop(0) if prompts else '')
        else: d.accept()
    pg.on('dialog', on_dialog)
    def go(page): pg.click(f'.nav-item[data-page="{page}"]'); time.sleep(0.3)
    def kpi(): return pg.inner_text('#kpiStudents').strip()
    def api_state():
        tok = pg.evaluate("localStorage.getItem('masar_token')")
        r = urllib.request.Request(URL + 'api/state', headers={'Authorization': 'Bearer ' + tok})
        return json.loads(urllib.request.urlopen(r).read())['state']
    def add_student(name, grade, section, gphone):
        pg.evaluate("openAddModal('student')"); time.sleep(0.3)
        pg.fill('#mStuName', name); pg.fill('#mStuAge', '12'); pg.fill('#mStuGuardianPhone', gphone); pg.fill('#mStuGuardianName', 'ولي أمر ' + name)
        pg.select_option('#mStuGrade', grade); pg.select_option('#mStuSection', section)
        pg.click('button[onclick*="submitAddModal"]'); time.sleep(0.8)

    pg.goto(URL); pg.wait_for_selector('#loginOverlay')
    check('U01 الشاشة فيها خياران: تسجيل الدخول + إنشاء حساب مدير', pg.locator('#tabLogin').count() == 1 and pg.locator('#tabSignup').count() == 1)
    pg.click('#tabSignup'); pg.fill('#suName', 'المدير'); pg.fill('#suSchool', 'مدرسة الاختبار'); pg.fill('#suUser', 'admin'); pg.fill('#suPass', 'Admin#12345'); pg.fill('#suCode', 'WRONG'); pg.click('#suBtn'); time.sleep(0.8)
    check('U01b رمز خاطئ: رسالة خطأ ولا يُنشأ حساب', 'غير صحيح' in pg.inner_text('#loginErr') and pg.locator('#loginOverlay').count() == 1)
    pg.fill('#suCode', 'CODE-123'); pg.click('#suBtn'); time.sleep(1.5)
    check('U02 الرمز صحيح: يُنشأ الحساب ويدخل مباشرة', pg.locator('#loginOverlay').count() == 0)
    check('U03 البداية: عداد الطلاب 0 ولا بيانات تجريبية', kpi() == '0' and len(api_state()['students']) == 0)
    go('students'); check('U04 قائمة الطلاب فارغة', pg.locator('#studentList .person-row').count() == 0)

    add_student('اختبار مِسار 001', 'السادس', 'أ', '0926853251')
    check('U05 إضافة طالب ⇒ العداد = 1 (بدون Refresh)', kpi() == '1', kpi())
    check('U06 الطالب ظاهر في قائمة الطلاب', pg.locator('#studentList .person-row').count() == 1)
    add_student('طالب ثاني', 'السابع', 'أ', '0911000000')
    check('U07 طالب ثاني ⇒ العداد = 2', kpi() == '2', kpi())
    add_student('طالب ثالث', 'السادس', 'أ', '0911222333')
    check('U08 ثالث ⇒ العداد = 3', kpi() == '3')
    pg.fill('#studentFilter', 'مِسار 001'); time.sleep(0.3)
    check('U09 البحث بالاسم يجد الطالب المضاف', pg.locator('#studentList .person-row').count() == 1 and '001' in pg.inner_text('#studentList'))
    pg.fill('#studentFilter', '')
    pg.reload(); pg.wait_for_selector('.nav-item'); time.sleep(1.5)
    check('U10 بعد Refresh: الطلاب باقون والعداد 3 (لا اختفاء)', kpi() == '3' and len(api_state()['students']) == 3)

    # ---- الحضور: الصف السادس أ
    go('attendance'); time.sleep(0.4)
    names = pg.inner_text('#attList')
    check('U11 الحضور: صف السادس أ يعرض طلابه فقط (001 وثالث) وليس طالب السابع', 'اختبار مِسار 001' in names and 'طالب ثالث' in names and 'طالب ثاني' not in names)
    row = pg.locator('#attList .att-row', has_text='اختبار مِسار 001'); row.locator('.att-btn.absent').click(); time.sleep(0.8)
    st = api_state(); sid = [x for x in st['students'] if x['name'] == 'اختبار مِسار 001'][0]['id']
    check('U12 الغياب محفوظ في DB (الطالب+التاريخ+الحالة)', st['attendanceHistory'].get(date.today().isoformat(), {}).get(str(sid)) == 'absent')
    check('U13 إشعار لولي الأمر في outbox', any(n['event'] == 'ABSENCE_CREATED' and n['phone'] == '218926853251' for n in st['notificationOutbox']))
    check('U14 عداد الغياب = 1 في الواجهة', pg.inner_text('#attAbsentN').strip() == '1')
    pg.reload(); pg.wait_for_selector('.nav-item'); time.sleep(1.5); go('attendance')
    check('U15 بعد Refresh: الغياب باقٍ', pg.locator('#attList .att-row', has_text='اختبار مِسار 001').locator('.att-btn.absent.on').count() == 1)
    go('reports'); time.sleep(0.4)
    check('U16 السجل السنوي يعرض الطالب الغائب', 'اختبار مِسار 001' in pg.inner_text('#annualAttendanceBody'))

    # ---- المالية
    go('finance')
    prompts[:] = ['مِسار 001', '1000', yest, '1']; pg.evaluate('addInstallment()'); time.sleep(1)
    check('U17 قسط جديد يظهر في الجدول ومتأخر', '1,000' in pg.inner_text('#finDues') and 'متأخ' in pg.inner_text('#finDues'))
    time.sleep(2.5)
    st = api_state(); check('U18 الـ Worker (بدون فتح صفحة المالية) أنشأ إشعار التأخير', any(n['event'] == 'PAYMENT_OVERDUE' and n['status'] == 'pending' for n in st['notificationOutbox']))
    prompts[:] = ['400']; pg.locator('#finDues button', has_text='تسجيل').first.click(); time.sleep(1)
    check('U19 دفع 400 ⇒ المتبقي 600', '600' in pg.inner_text('#finDues') and '400' in pg.inner_text('#finDues'))
    pg.locator('#finDues button', has_text='سداد').first.click(); time.sleep(1)   # سداد كامل (Mark Paid)
    st = api_state()
    check('U20 سداد كامل ⇒ إشعار التأخير ملغى', all(n['status'] == 'cancelled' for n in st['notificationOutbox'] if n['event'] == 'PAYMENT_OVERDUE'))
    check('U21 سداد كامل ⇒ قسط جديد تلقائي (ظاهر في الجدول) + رسالة لولي الأمر', len(st['dues']) == 2 and any(n['event'] == 'INSTALLMENT_RENEWED' and 'آخر موعد' in n['message'] for n in st['notificationOutbox']) and pg.locator('#finDues tbody tr').count() == 2)
    check('U22 المعاملات المالية مسجلة', len(st['transactions']) == 2 and 'سداد' in pg.inner_text('#finTransactions'))

    # ---- الدرجات (10 مواد)
    go('grades'); pg.evaluate("toggleAddRow()")
    opts = pg.eval_on_selector_all('#gradeSubject option', 'els=>els.map(e=>e.textContent)')
    check('U23 قائمة المواد 10 على الأقل', len(opts) >= 10, len(opts))
    pg.select_option('#gradeStudent', label=[o for o in pg.eval_on_selector_all('#gradeStudent option', 'els=>els.map(e=>e.textContent)') if '001' in o][0])
    for i, o in enumerate(opts[:10]):
        pg.select_option('#gradeSubject', label=o); pg.fill('#gradeValue', str(60 + i)); pg.click('button[onclick="confirmAddGrade()"]'); time.sleep(0.5)
    check('U24 عشر درجات محفوظة في DB وتظهر في الجدول', len([g for g in api_state()['gradesData'] if g['student'] == 'اختبار مِسار 001']) == 10 and pg.locator('#gradesBody tr').count() == 10)

    # ---- الجدول
    go('schedule')
    prompts[:] = ['الرياضيات', 'أ. علي', 'السادس أ', 'الأحد', '٨:٠٠ - ٨:٥٠', '٨:٥٠', '101']; pg.evaluate('addCourseUI()'); time.sleep(1)
    check('U25 الحصة تظهر في الجدول من DB', 'الرياضيات' in pg.inner_text('#schedTable') and len(api_state()['courses']) == 1)
    prompts[:] = ['العلوم', 'أ. علي', 'السادس أ', 'الأحد', '٩:٤٠ - ١٠:٣٠', '١٠:٣٠', '102']; pg.evaluate('addCourseUI()'); time.sleep(1)
    check('U26 حصة بتوقيت «٩:٤٠ - ١٠:٣٠» تُقبل (خطأ المقارنة النصية مُصلَح)', len(api_state()['courses']) == 2)

    # ---- المعلمون
    go('teachers'); pg.evaluate("openAddModal('teacher')"); time.sleep(0.3)
    pg.fill('#mTchName', 'أ. علي'); pg.fill('#mTchSubject', 'الرياضيات'); pg.click('button[onclick*="submitAddModal"]'); time.sleep(0.8)
    check('U27 معلم يُحفظ ويظهر + عداد المعلمين', pg.locator('#teacherList .person-row').count() == 1 and len(api_state()['teachers']) == 1)

    # ---- التقارير
    go('reports'); pg.evaluate("openReport('finance')"); time.sleep(0.6)
    check('U28 تقرير مالي من DB يعرض الطالب والمبالغ', 'اختبار مِسار 001' in pg.inner_text('#reportOverlay') and '1,000' in pg.inner_text('#reportOverlay'))
    pg.evaluate("document.getElementById('reportOverlay').style.display='none'")
    pg.evaluate("openReport('grades')"); time.sleep(0.5); check('U29 تقرير الدرجات 10 صفوف', pg.locator('#reportOverlay tbody tr').count() == 10)
    pg.evaluate("document.getElementById('reportOverlay').style.display='none'")

    # ---- إعادة تشغيل السيرفر + المتصفح
    stop(); start(); pg.reload(); pg.wait_for_selector('.nav-item'); time.sleep(2)
    st = api_state()
    check('U30 بعد إعادة تشغيل السيرفر: الطلاب3/الأقساط2/الدرجات10/الحصص2/المعلم1/الحضور', len(st['students']) == 3 and len(st['dues']) == 2 and len(st['gradesData']) == 10 and len(st['courses']) == 2 and len(st['teachers']) == 1 and st['attendanceHistory'])
    check('U31 الواجهة بعد الإقلاع تعرض 3 طلاب', kpi() == '3')
    # ---- حذف
    go('students'); pg.locator('#studentList .person-row', has_text='طالب ثاني').click(); time.sleep(0.3)
    prompts[:] = ['طالب ثاني']; pg.click('button[onclick*="deleteStudentForever"]'); time.sleep(1)
    check('U32 حذف نهائي من الواجهة يعمل ويقل العداد إلى 2', kpi() == '2' and len(api_state()['students']) == 2)
    pg.locator('#studentList .person-row', has_text='طالب ثالث').click(); time.sleep(0.3); pg.click('button[onclick*="archiveStudent"]'); time.sleep(1)
    check('U33 الأرشفة تخفي الطالب من القائمة النشطة', kpi() == '1')
    # ---- خروج / صلاحية
    pg.evaluate("logout()"); time.sleep(1.2); pg.wait_for_selector('#loginOverlay')
    check('U34 بعد الخروج تظهر شاشة الدخول ولا تظهر بيانات', 'مِسار' in pg.inner_text('#loginOverlay h2'))
    pg.fill('#liUser', 'admin'); pg.fill('#liPass', 'bad'); pg.click('#liBtn'); time.sleep(0.6)
    check('U35 كلمة مرور خاطئة تعرض خطأ', 'غير صحيحة' in pg.inner_text('#loginErr'))
    pg.fill('#liPass', 'Admin#12345'); pg.click('#liBtn'); time.sleep(1.5)
    check('U36 دخول صحيح يرجع البيانات', pg.locator('#loginOverlay').count() == 0 and kpi() == '1')
    ls = pg.evaluate("Object.keys(localStorage)")
    check('U37 localStorage لا يحوي أي بيانات مدرسة (فقط masar_token)', ls == ['masar_token'], ls)
    check('U38 صفر أخطاء JavaScript أثناء كل المسارات', not errs, errs)
    b.close()
finally:
    try: stop()
    except Exception: pass
f = [n for n, ok in RES if not ok]
print(f'\nTOTAL {len(RES)}  PASS {len(RES) - len(f)}  FAIL {len(f)}')
for n in f: print('FAILED:', n)
sys.exit(1 if f else 0)
