# بوابة إدارة الحضور والانصراف

مشروع ويب عربي مبني باستخدام Django لإدارة حضور وانصراف الموظفين، وتنظيم البيانات المؤسسية، ومتابعة المخالفات، وإعداد التقارير وسجل التدقيق.

## المتطلبات

- Python 3.13 أو إصدار مدعوم من Django 5.2
- Git

## إنشاء البيئة الافتراضية

من داخل مجلد المشروع، نفّذ في PowerShell:

```powershell
py -3.13 -m venv .venv
```

يمكن تشغيل Python داخل البيئة مباشرةً دون تعديل سياسة تشغيل PowerShell:

```powershell
.\.venv\Scripts\python.exe --version
```

## تثبيت الحزم

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## إعداد متغيرات البيئة

انسخ `.env.example` إلى `.env`، ثم عيّن قيمة قوية وفريدة للمتغير `DJANGO_SECRET_KEY`. ملف `.env` مستبعد من Git ولا ينبغي رفعه إلى المستودع.

## تشغيل المشروع

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

بعد التشغيل، افتح `http://127.0.0.1:8000/` في المتصفح.
