# استعادة نسخة احتياطية والسيرفر متوقف:  python restore_backup.py اسم_الملف.db
import sys, server
if len(sys.argv) < 2:
    print('الاستخدام: python restore_backup.py <اسم ملف من مجلد backups>'); sys.exit(1)
server.do_restore(sys.argv[1]); print('تمت الاستعادة (وحُفظت نسخة قبلها).')
