#!/usr/bin/env bash
set -e; cd "$(dirname "$0")/.."
python3 -m py_compile server.py
python3 - <<'PY'
import re,subprocess
s=open('masar.html',encoding='utf-8').read()
open('/tmp/masar_check.js','w',encoding='utf-8').write('\n'.join(re.findall(r'<script[^>]*>(.*?)</script>',s,re.S)))
subprocess.check_call(['node','--check','/tmp/masar_check.js']); print('JS syntax OK')
PY
python3 tests/e2e_api.py; python3 tests/e2e_robust.py; python3 tests/e2e_whatsapp_inbound.py; python3 tests/e2e_ui.py
