# CHANGELOG
| التعديل | لماذا | ماذا تغير | الاختبار |
|---|---|---|---|
| توحيد التخزين | اختفاء البيانات/تعدد المخازن | SQLite + API فقط، حذف localStorage والمزامنة القديمة | VERIFIED |
| الطلاب بمعرّفات | الاسم كان المفتاح | students/guardians/classes + FK | VERIFIED |
| الحضور | لا سجل مركزي | جدول attendance + إشعار غياب | VERIFIED |
| الأقساط | إشعارات غير مستقرة | installments/payments + overdue + تجديد تلقائي | VERIFIED |
| Worker/outbox | يعمل والمتصفح مغلق | thread + dedupe + retry + إلغاء عند السداد | VERIFIED (Graph مُحاكى) |
| واتساب إرسال | كان واجهة فقط | adapter قالب/نص | WRITTEN-ONLY مع Meta |
| واتساب استقبال | غير موجود | webhook موقّع + inbox مربوط بالطالب | VERIFIED محاكاة / WRITTEN-ONLY مع Meta |
| المستخدمون والأدوار | صفحة مفتوحة | users/sessions + RBAC في السيرفر | VERIFIED |
| التقارير | أزرار وهمية | /api/reports من DB + طباعة | VERIFIED |
| النسخ الاحتياطي | لا يوجد | يومي + يدوي + استعادة | VERIFIED |
| أمان | سر في HTML، XSS | إزالة المفتاح، رفض <> ، .env | VERIFIED |
| حذف المساعد الذكي | يكتب خارج DB | أُزيل | — |
| الدخول وإنشاء الحساب | طلب المالك | شاشة بخيارين، إنشاء مدير برمز MASAR_ACCESS_CODE، قفل 5 محاولات/5 دقائق، حذف /api/setup | VERIFIED |
