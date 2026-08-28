# التصميم المقترح لقاعدة بيانات بوابة الحضور والانصراف

## بيانات الوثيقة ونطاقها

| البند | القيمة |
|---|---|
| المرحلة | تصميم قاعدة البيانات فقط |
| الإصدار المستهدف | الإصدار الأول V1 |
| قاعدة الإنتاج | PostgreSQL |
| قاعدة التطوير المحلي | SQLite مع مراعاة الفروق الموثقة |
| لغة الواجهة | العربية بالكامل |
| تسمية الجداول والحقول | الإنجليزية |
| حالة الوثيقة | تصميم مقترح يحتاج اعتماد القرارات المفتوحة قبل التنفيذ |

هذه الوثيقة تصميم منطقي وفيزيائي مقترح، وليست Models أو Migrations. تعتمد القرارات المثبتة في `AGENTS.md` و`PROJECT_PLAN.md`، وبالأخص: استخدام UUID، وCustom User Model، والمطابقة بالسجل المدني، وعدم تعديل البيانات الأصلية المستوردة، وفصل المعالجات الإدارية، وتسجيل العمليات الحساسة، ووضع منطق الأعمال في Services.

## 1. مبادئ التصميم المعتمدة

1. كل جدول مملوك للمشروع له مفتاح أساسي `id UUID` غير قابل للتغيير، ويولد بصيغة UUID v4.
2. السجل المدني معرف أعمال للمطابقة فقط، ولا يستخدم مفتاحًا أساسيًا أو في الروابط أو أسماء الملفات أو السجلات النصية.
3. بيانات Excel الأصلية تحفظ مشفرة وغير قابلة للتعديل؛ التصحيح أو المعالجة يحفظ في جدول مستقل.
4. البيانات التاريخية المهمة لا تحذف حذفًا عاديًا؛ تستخدم الحالة أو الأرشفة أو Soft Delete وفق نوع الجدول.
5. العلاقات التاريخية تملك `valid_from` و`valid_to`، ولا تستنتج من الحالة الحالية للموظف.
6. كل وقت لحظي يخزن كـ `TIMESTAMPTZ`، وكل يوم عمل كـ `DATE`، وتعرض الأوقات وفق `Asia/Riyadh`.
7. تستخدم `JSONB` في PostgreSQL للحمولات المرنة واللقطات التفسيرية فقط، وليس بديلًا عن الأعمدة والعلاقات الأساسية.
8. يطبق تقييد مسؤول القسم على الخادم من خلال نطاقات الأقسام الفعالة، وليس من الواجهة وحدها.
9. العمليات المركبة، مثل اعتماد الاستيراد وتطبيق المعالجة، تنفذ داخل Transaction في طبقة Services.
10. الجداول غير القابلة للتعديل لا تحتوي `updated_at` أو `updated_by` إلا إذا كان هناك سبب حقيقي لتغير حالتها؛ أحداثها اللاحقة تحفظ كسجلات جديدة.

### القرار المعماري DB-001: معيار UUID

- يعتمد **UUID v4** مفتاحًا أساسيًا لجميع الجداول المملوكة للمشروع دون استثناء.
- ينشأ UUID v4 داخل التطبيق، ولا يحمل معنى وظيفيًا ولا يستخدم بدل قيود الأعمال الفريدة.
- تستثنى فقط جداول Django الداخلية الجاهزة التي يديرها إطار العمل، مثل `django_migrations` و`django_content_type` و`django_admin_log` وجداول الجلسات والصلاحيات الداخلية عند استخدامها.
- هذا الاستثناء تقني خاص بإطار العمل، ولا يدخل في علاقات كيانات الأعمال ولا يؤثر على منطق النظام أو شرط UUID في أي جدول نملكه.
- لا تعد UUID وسيلة تفويض؛ تبقى المصادقة والصلاحية والنطاق التنظيمي إلزامية لكل عملية.

### القرار المعماري DB-002: حماية السجل المدني

- تخزن القيمة الأصلية للسجل المدني مشفرة في `national_id_encrypted` باستخدام AES-256-GCM ومفتاح `PII_ENCRYPTION_KEY` المدار خارج قاعدة البيانات.
- تنشأ قيمة HMAC-SHA256 منفصلة في `national_id_hash` باستخدام `NATIONAL_ID_HMAC_KEY` مختلف، وتستخدم وحدها للمطابقة وفرض عدم التكرار.
- يطبع السجل المدني قبل HMAC بتحويل الأرقام العربية والفارسية إلى إنجليزية، وإزالة المسافات والرموز، ثم التحقق من أنه عشرة أرقام.
- يحمل كل سجل مشفر `encryption_key_version` حتى يمكن ربطه بالمفتاح الصحيح عند تدوير المفاتيح مستقبلًا.
- لا يستخدم السجل المدني، مشفرًا أو ظاهرًا، مفتاحًا أساسيًا أو جزءًا من روابط الواجهة أو السجلات النصية أو أسماء الملفات.
- لا تسجل القيمة الأصلية في Logs أو Audit Log أو رسائل الأخطاء؛ يسمح فقط بعرض مقنع وفق الصلاحية.
- لا يوجد مفتاح افتراضي عند غياب متغيرات البيئة؛ تفشل ميزة الاستيراد برسالة إعداد عربية آمنة دون كشف تفاصيل المفتاح.
- تبقى دورة التدوير والاستعادة إجراءً تشغيليًا يجب توثيقه قبل الإنتاج، بينما خوارزمية الحماية وأسماء المتغيرات معتمدة.

### القرار المعماري DB-003: استيراد بيانات الموظفين

- تعتمد جداول مستقلة هي `employee_import_batches`, `employee_import_rows`, و`employee_import_errors` لأن حمولة بيانات الموظفين وسياسة اعتمادها تختلفان عن استيراد الحضور الأسبوعي.
- يحفظ ملف XLSX نفسه مشفرًا بـAES-256-GCM باسم تخزين UUID عشوائي وامتداد غير تنفيذي خارج المسار التنفيذي، وتشفّر كذلك الحمولة الأصلية لكل صف.
- المعاينة لا تعدل بيانات الموظفين، ويطبق الاعتماد مرة واحدة داخل `transaction.atomic`.
- الرقم الوظيفي اختياري لعدم وجوده في القالب الحالي، ويكون فريدًا عند وجوده، ولا يولد النظام رقمًا وظيفيًا وهميًا.
- أعمدة القالب المعتمدة في هذه الدفعة هي: اسم الموظف، السجل المدني، رقم الجوال، القسم، مكان الحضور والانصراف، المدير المباشر، والسجل المدني للمدير المباشر. تعتمد المطابقة على أسماء الأعمدة لا ترتيبها.
- عند اختيار إنشاء المراجع المفقودة صراحة أثناء الاعتماد، ينشأ القسم كنوع `department` والموقع كنوع موقع عمل، مع رمز داخلي عشوائي يبدأ بـ`IMP-DEPT-` أو `IMP-LOC-`؛ لا يعد هذا رقمًا وظيفيًا ولا يستخدم للمطابقة مع الموظف.

## 2. اصطلاحات أنواع الحقول

| النوع في الوثيقة | المعنى المقترح |
|---|---|
| UUID | معرف UUID v4 أصلي في PostgreSQL للجداول المملوكة للمشروع |
| VARCHAR(n) | نص محدود الطول |
| TEXT | نص طويل |
| BOOLEAN | قيمة منطقية |
| SMALLINT / INTEGER / BIGINT | رقم صحيح بحسب الحجم المتوقع |
| DECIMAL(p,s) | رقم عشري مضبوط |
| DATE | تاريخ دون وقت |
| TIME | وقت محلي ضمن تعريف المناوبة |
| TIMESTAMPTZ | وقت لحظي واعٍ بالمنطقة الزمنية |
| JSONB | بنية JSON قابلة للفهرسة في PostgreSQL |
| BYTEA | بيانات مشفرة أو ثنائية |
| INET | عنوان شبكة في PostgreSQL |
| ENUM منطقي | حقل نصي محدود بقائمة خيارات يفرضها التطبيق وCheck Constraint |

حقول المستخدم المرجعية `created_by` و`updated_by` تشير إلى `users.id` بسياسة `SET NULL` حتى لا يؤدي تعطيل المستخدم أو إخفاؤه إلى فقدان السجل التاريخي. لا تحذف حسابات المستخدمين فعليًا في التشغيل المعتاد.

تعتمد أسماء الجداول الإنجليزية بصيغة الجمع و`snake_case`، وتسمى المفاتيح الخارجية بصيغة `<entity>_id`. يستخدم الاسم `department_scope_id` فقط عندما يمثل الحقل لقطة لنطاق صلاحية أو تقرير، بينما يستخدم `department_id` عندما تكون العلاقة قسمًا تشغيليًا مباشرًا؛ هذا فرق دلالي مقصود وليس اختلاف تسمية.

## 3. قائمة جداول الإصدار الأول

### الحسابات والصلاحيات

1. `users`
2. `roles`
3. `permissions`
4. `role_permissions`
5. `user_roles`
6. `user_department_scopes`

### المؤسسة والموظفون

7. `departments`
8. `locations`
9. `job_titles`
10. `employees`
11. `employee_identities`
12. `employment_assignments`
13. `employee_primary_locations`

### استيراد بيانات الموظفين

14. `employee_import_batches`
15. `employee_import_rows`
16. `employee_import_errors`

### سياسات الدوام والجداول

17. `work_policies`
18. `shifts`
19. `employee_shift_assignments`
20. `holiday_calendars`
21. `holidays`

### الاستيراد والحضور

22. `operational_periods`
23. `import_batches`
24. `import_rows`
25. `import_errors`
26. `raw_attendance_records`
27. `calculation_runs`
28. `daily_attendance_results`
29. `daily_attendance_sources`
30. `administrative_adjustments`
31. `period_locks`

### المخالفات والمعالجات والاعتمادات

32. `violation_types`
33. `violations`
34. `resolution_requests`
35. `resolution_attachments`
36. `approval_workflows`
37. `approval_workflow_steps`
38. `approval_instances`
39. `approval_step_instances`
40. `approval_decisions`

### الخدمات المشتركة

41. `notifications`
42. `report_exports`
43. `audit_logs`
44. `system_settings`

إضافة فترة التشغيل وجداول استيراد الموظفين رفعت العدد المنطقي إلى 44 جدولًا. هذا لا يعني تنفيذها معًا؛ يظل التنفيذ مرحليًا، وتبقى كل دفعة محصورة في نطاقها المعتمد.

## 4. الحقول التفصيلية: الحسابات والصلاحيات

### 4.1 جدول `users`

Custom User Model لتسجيل الدخول باسم المستخدم وكلمة المرور. لا توضع بيانات الموظف الوظيفية داخله، ولا ينشأ حساب تلقائيًا لكل موظف؛ ينشئ مستخدم مخول إداريًا الحساب فقط عند وجود حاجة فعلية للدخول إلى النظام.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| username | VARCHAR(150) | نعم | اسم الدخول بعد التطبيع؛ `unique=True` لتلبية عقد Django |
| password | VARCHAR(128) | نعم | تجزئة كلمة المرور بصيغة Django، وليست كلمة المرور الأصلية |
| email | VARCHAR(254) | لا | البريد الإلكتروني |
| first_name | VARCHAR(150) | لا | الاسم الأول للعرض الإداري |
| last_name | VARCHAR(150) | لا | الاسم الأخير للعرض الإداري |
| is_active | BOOLEAN | نعم | يسمح أو يمنع الدخول |
| is_staff | BOOLEAN | نعم | يسمح بدخول الإدارة التقنية عند منح الصلاحيات |
| is_superuser | BOOLEAN | نعم | يستخدم في أضيق نطاق ممكن |
| must_change_password | BOOLEAN | نعم | يفرض تغيير كلمة المرور المؤقتة |
| failed_login_count | SMALLINT | نعم | عداد مساعد لسياسة القفل |
| locked_until | TIMESTAMPTZ | لا | نهاية القفل المؤقت |
| last_login | TIMESTAMPTZ | لا | آخر دخول ناجح |
| password_changed_at | TIMESTAMPTZ | لا | وقت آخر تغيير |
| locale | VARCHAR(10) | نعم | القيمة الافتراضية `ar` |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |
| updated_at | TIMESTAMPTZ | نعم | وقت آخر تعديل |
| created_by | UUID FK users | لا | منشئ الحساب، `SET NULL` |
| updated_by | UUID FK users | لا | آخر معدل، `SET NULL` |
| archived_at | TIMESTAMPTZ | لا | أرشفة الحساب بدل حذفه |

**القيود والفهارس:**

- `unique=True` على `username` مطلوب لأن الحقل هو `USERNAME_FIELD` في Django، ويضاف Unique وظيفي غير حساس لحالة الأحرف على `LOWER(username)` لتحقيق قاعدة العمل. الفهرسان متداخلان جزئيًا لكنهما مبرران بتوافق الإطار وقاعدة عدم حساسية الأحرف.
- فهرس على `(is_active, archived_at)` وعلى `LOWER(email)` للبحث فقط؛ البريد لا يكون فريدًا قبل اعتماد السياسة.
- Check: `failed_login_count >= 0`، و`locale = 'ar'` في V1 ما لم يعتمد تعدد اللغات.

### 4.2 جدول `roles`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| code | VARCHAR(80) | نعم | رمز ثابت مثل `department_manager` |
| name_ar | VARCHAR(150) | نعم | الاسم العربي |
| description_ar | TEXT | لا | الوصف العربي |
| is_system | BOOLEAN | نعم | يمنع حذف أو تغيير الدور الجوهري |
| is_active | BOOLEAN | نعم | حالة الدور |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `code`. يستخدم `is_active` لتعطيل الدور المرجعي البسيط دون حذفه، ولا يحتاج الجدول `archived_at`. لا يضاف فهرس مستقل على `is_active` ما لم تثبت فائدته بسبب انخفاض انتقائية الحقل.

### 4.3 جدول `permissions`

جدول مرجعي بسيط يستخدم `is_active` لتعطيل الصلاحية دون حذفها؛ لا يستخدم `archived_at`.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| code | VARCHAR(120) | نعم | رمز الصلاحية مثل `attendance.view_department` |
| module | VARCHAR(50) | نعم | الوحدة الوظيفية |
| action | VARCHAR(50) | نعم | الإجراء |
| name_ar | VARCHAR(150) | نعم | الاسم العربي |
| description_ar | TEXT | لا | شرح الصلاحية |
| is_active | BOOLEAN | نعم | حالة الصلاحية |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |

**القيود والفهارس:** Unique على `code` وUnique مركب على `(module, action)`، وفهرس على `(module, is_active)`.

### 4.4 جدول `role_permissions`

جدول وسيط لعلاقة ManyToMany بين الأدوار والصلاحيات.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| role_id | UUID FK roles | نعم | `PROTECT` لأن الدور لا يحذف فعليًا |
| permission_id | UUID FK permissions | نعم | `PROTECT` لأن الصلاحية لا تحذف فعليًا |
| granted_at | TIMESTAMPTZ | نعم | وقت المنح |
| granted_by | UUID FK users | لا | `SET NULL` |
| revoked_at | TIMESTAMPTZ | لا | وقت إلغاء المنح مع إبقاء تاريخه |
| revoked_by | UUID FK users | لا | منفذ الإلغاء، `SET NULL` |

**القيود والفهارس:** Partial Unique على `(role_id, permission_id) WHERE revoked_at IS NULL` لمنع منحين فعالين متكررين مع السماح بتاريخ إعادة المنح، وCheck يضمن أن `revoked_at >= granted_at`. فهارس على `(role_id, revoked_at)` و`(permission_id, revoked_at)`. لا يحذف الرابط عند سحب الصلاحية؛ يملأ `revoked_at` و`revoked_by` وتسجل العملية في Audit Log. يبقى `revoked_by` اختياريًا للسماح بإلغاء نظامي وللحفاظ على السجل إذا أزيل حساب الفاعل، بينما وقت الإلغاء هو مصدر حالة الرابط.

### 4.5 جدول `user_roles`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| user_id | UUID FK users | نعم | `PROTECT` تشغيليًا، مع تعطيل الحساب بدل حذفه |
| role_id | UUID FK roles | نعم | `PROTECT` للحفاظ على تاريخ الإسناد |
| valid_from | TIMESTAMPTZ | نعم | بداية النفاذ |
| valid_to | TIMESTAMPTZ | لا | نهاية النفاذ |
| is_active | BOOLEAN | نعم | تعطيل مبكر للإسناد |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |
| created_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `(user_id, role_id, valid_from)`، وCheck أن `valid_to > valid_from`، وفهرس على `(user_id, is_active, valid_from, valid_to)`.

### 4.6 جدول `user_department_scopes`

يحدد الأقسام التي يستطيع المستخدم الوصول إليها. مسؤول القسم يمنح نطاق قسمه فقط. شمول الأقسام الفرعية قرار صريح في الحقل ولا يفترض تلقائيًا.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| user_id | UUID FK users | نعم | `PROTECT` |
| department_id | UUID FK departments | نعم | `PROTECT` |
| role_id | UUID FK roles | لا | الدور الذي منح النطاق، `PROTECT` |
| include_descendants | BOOLEAN | نعم | القيمة الافتراضية `False`؛ لا يشمل الأقسام الفرعية إلا بتفعيل صريح |
| access_level | ENUM منطقي | نعم | `view` أو `manage` أو `approve` |
| valid_from | TIMESTAMPTZ | نعم | بداية النفاذ |
| valid_to | TIMESTAMPTZ | لا | نهاية النفاذ |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |
| created_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** لتفادي سماح PostgreSQL بتكرار Null في `role_id`، يستخدم قيدان: Partial Unique على `(user_id, department_id, access_level, valid_from) WHERE role_id IS NULL`، وPartial Unique على `(user_id, department_id, role_id, access_level, valid_from) WHERE role_id IS NOT NULL`. يضاف Check للفترة، وفهارس على `(user_id, valid_from, valid_to)` و`(department_id, valid_from, valid_to)`. لا يضاف فهرس منفرد على `department_id` لأن الفهرس المركب يبدأ به.

## 5. الحقول التفصيلية: المؤسسة والموظفون

### 5.1 جدول `departments`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| code | VARCHAR(50) | نعم | رمز القسم أو الوحدة |
| name_ar | VARCHAR(200) | نعم | الاسم العربي |
| unit_type | ENUM منطقي | نعم | قطاع، إدارة، قسم، وحدة |
| parent_id | UUID FK departments | لا | الوحدة الأم، `PROTECT` |
| path_cache | VARCHAR(1000) | لا | مسار مساعد للقراءة؛ يعاد بناؤه عبر Service |
| level | SMALLINT | نعم | مستوى الشجرة |
| is_active | BOOLEAN | نعم | الحالة الحالية |
| valid_from | DATE | نعم | بداية نفاذ الوحدة |
| valid_to | DATE | لا | نهاية نفاذها |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |
| archived_at | TIMESTAMPTZ | لا | أرشفة |

**القيود والفهارس:** Unique على `code`، وCheck يمنع `parent_id = id` ويضمن الفترة، وفهرس على `parent_id` وعلى `(is_active, archived_at)` وعلى `path_cache` عند اعتماد أسلوب Materialized Path.

### 5.2 جدول `locations`

جدول مرجعي بسيط يستخدم `is_active` للإيقاف. تبقى المراجع التاريخية إليه محمية بسياسة `PROTECT`، لذلك لا يلزم `archived_at`.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| code | VARCHAR(50) | نعم | رمز الموقع |
| name_ar | VARCHAR(200) | نعم | الاسم العربي |
| location_type | ENUM منطقي | نعم | مقر، فرع، موقع ميداني |
| department_id | UUID FK departments | لا | الجهة المالكة، `PROTECT` |
| address_ar | TEXT | لا | العنوان |
| latitude | DECIMAL(9,6) | لا | إحداثي مستقبلي للمقارنة |
| longitude | DECIMAL(9,6) | لا | إحداثي مستقبلي للمقارنة |
| timezone | VARCHAR(50) | نعم | `Asia/Riyadh` في V1 |
| is_active | BOOLEAN | نعم | الحالة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `code`، وCheck للإحداثيات، وفهرس على `(department_id, is_active)`.

### 5.3 جدول `job_titles`

جدول مرجعي بسيط؛ التعطيل يتم عبر `is_active` مع إبقاء السجلات التاريخية التي تشير إلى المسمى.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| code | VARCHAR(50) | نعم | رمز المسمى |
| name_ar | VARCHAR(200) | نعم | المسمى العربي |
| is_active | BOOLEAN | نعم | الحالة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `code`. يراجع الاحتياج لفهرس `is_active` بعد قياس الاستعلامات؛ لا يضاف افتراضيًا لقلة انتقائيته.

### 5.4 جدول `employees`

الموظف كيان مستقل عن حساب الدخول. يمكن إنشاء الموظف وإدارته واستيراد حضوره دون وجود حساب مستخدم، ولا ينشأ الحساب تلقائيًا عند إنشاء الموظف. تبقى العلاقة مع `users` اختيارية OneToOne، ولا يربط الحساب إلا مستخدم يملك صلاحية إدارية مخصصة.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي، وليس السجل المدني |
| employee_number | VARCHAR(50) | لا | الرقم الوظيفي؛ اختياري وفريد عند وجوده ولا يولد تلقائيًا |
| user_id | UUID FK users | لا | OneToOne اختياري، ينشأ الربط عند الحاجة فقط، `SET NULL` |
| full_name_ar | VARCHAR(250) | نعم | الاسم العربي |
| preferred_name_ar | VARCHAR(150) | لا | اسم العرض |
| work_email | VARCHAR(254) | لا | البريد الوظيفي |
| mobile_masked | VARCHAR(30) | لا | رقم مقنع عند الحاجة |
| employment_status | ENUM منطقي | نعم | نشط، موقوف، منتهي خدمة، مؤرشف |
| hire_date | DATE | لا | تاريخ التعيين |
| termination_date | DATE | لا | تاريخ انتهاء الخدمة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |
| archived_at | TIMESTAMPTZ | لا | Soft Delete/أرشفة |

**القيود والفهارس:** Partial Unique على `employee_number` عندما لا يكون Null، وUnique على `user_id` عندما لا يكون Null، وCheck للتواريخ، وفهارس على `(employment_status, archived_at)` وعلى `full_name_ar` للبحث الملائم.

### 5.5 جدول `employee_identities`

علاقة OneToOne مع الموظف في V1. تفصل الهوية الحساسة عن الملف الوظيفي وتتيح تشديد صلاحيات الوصول.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| employee_id | UUID FK employees | نعم | OneToOne، `PROTECT` |
| identity_type | ENUM منطقي | نعم | `national_id` في V1 |
| national_id_hash | CHAR(64) | نعم | HMAC أو بصمة مفاتيحية للمطابقة والفريدية |
| national_id_encrypted | BYTEA | نعم | القيمة الأصلية مشفرة عند الحاجة للاسترجاع |
| encryption_key_version | VARCHAR(30) | نعم | إصدار مفتاح AES-256-GCM المستخدم |
| national_id_last4 | CHAR(4) | نعم | للعرض المقنع فقط |
| normalized_length | SMALLINT | نعم | تحقق مساعد دون كشف القيمة |
| verified_at | TIMESTAMPTZ | لا | وقت التحقق |
| verification_source | VARCHAR(50) | لا | يدوي أو استيراد أو تكامل |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `employee_id`، وUnique على `national_id_hash`، وCheck على `identity_type` و`national_id_last4` و`normalized_length`. لا يفهرس النص المشفر.

### 5.6 جدول `employment_assignments`

السجل التاريخي لانتقالات الموظف بين الأقسام والمناصب والمديرين.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| employee_id | UUID FK employees | نعم | `PROTECT` |
| department_id | UUID FK departments | نعم | `PROTECT` |
| job_title_id | UUID FK job_titles | لا | `PROTECT` |
| manager_employee_id | UUID FK employees | لا | المدير المباشر، `PROTECT` |
| assignment_type | ENUM منطقي | نعم | أساسي، تكليف، ندب |
| valid_from | DATE | نعم | بداية النفاذ |
| valid_to | DATE | لا | نهاية النفاذ |
| is_primary | BOOLEAN | نعم | الإسناد الوظيفي الأساسي |
| reason | TEXT | لا | سبب النقل أو التغيير |
| reference_number | VARCHAR(100) | لا | مرجع القرار دون بيانات حساسة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `(employee_id, valid_from, assignment_type, department_id)`، وCheck للفترة ولمنع أن يكون الموظف مدير نفسه. في PostgreSQL يوصى بـ Exclusion Constraint يمنع تداخل إسنادين أساسيين نشطين للموظف؛ وفي SQLite يطبق التحقق داخل Service واختبارات المعاملة. فهارس على `(employee_id, valid_from, valid_to)` و`(department_id, valid_from, valid_to)` و`manager_employee_id`.

### 5.7 جدول `employee_primary_locations`

مكان التوقيع الأساسي للموظف وتاريخه. يستخدم المكان النافذ في تاريخ الحضور، لا المكان الحالي.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| employee_id | UUID FK employees | نعم | `PROTECT` |
| location_id | UUID FK locations | نعم | `PROTECT` |
| valid_from | DATE | نعم | بداية النفاذ |
| valid_to | DATE | لا | نهاية النفاذ |
| assignment_reason | TEXT | لا | سبب الإسناد أو النقل |
| reference_number | VARCHAR(100) | لا | مرجع إداري |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `(employee_id, location_id, valid_from)`، وCheck للفترة. يوصى بـ Exclusion Constraint يمنع تداخل موقعين أساسيين للموظف. فهرس على `(employee_id, valid_from, valid_to)` وعلى `(location_id, valid_from, valid_to)`.

### 5.8 جدول `employee_import_batches`

رأس عملية استيراد بيانات الموظفين. يمنع تكرار الملف ببصمته، ولا ينفذ الاعتماد أكثر من مرة.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| original_filename | VARCHAR(255) | نعم | اسم منقى للعرض فقط |
| storage_key | VARCHAR(500) | نعم | اسم UUID عشوائي خارج المسار التنفيذي |
| file_sha256 | CHAR(64) | نعم | بصمة منع تكرار الملف |
| file_size_bytes | BIGINT | نعم | الحجم المتحقق منه |
| mime_type | VARCHAR(100) | نعم | النوع الفعلي المتحقق منه |
| encryption_key_version | VARCHAR(30) | نعم | إصدار مفتاح تشفير ملف XLSX المحفوظ |
| status | ENUM منطقي | نعم | مرفوع، جاهز للمعاينة، به أخطاء، معتمد، أو فشل |
| summary counters | INTEGER | نعم | إجمالي الصفوف والجديد والتحديث والمراجع المفقودة والأخطاء والتحذيرات |
| uploaded_by / approved_by | UUID FK users | لا | `SET NULL` |
| approved_at | TIMESTAMPTZ | لا | وقت الاعتماد الوحيد |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |

**القيود والفهارس:** Unique على `storage_key` و`file_sha256`، وCheck للعدادات والحجم، وفهرس على `(status, created_at)`.

### 5.9 جدول `employee_import_rows`

يحفظ الصف الأصلي مشفرًا ولا يعدّل بيانات الموظف أثناء المعاينة.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| batch_id | UUID FK employee_import_batches | نعم | `PROTECT` |
| row_number | INTEGER | نعم | رقم الصف في Excel |
| raw_payload_encrypted | BYTEA | نعم | الحمولة الأصلية مشفرة بـAES-256-GCM |
| encryption_key_version | VARCHAR(30) | نعم | إصدار مفتاح التشفير |
| payload_sha256 | CHAR(64) | نعم | بصمة الحمولة الأصلية |
| national_id_hash | CHAR(64) | لا | HMAC للمطابقة فقط |
| national_id_last4 | CHAR(4) | لا | عرض مقنع |
| display_data_json | JSONB | نعم | بيانات منقحة لا تحتوي السجل المدني الكامل |
| import_action | ENUM منطقي | نعم | إنشاء، تحديث، أو تخطي |
| validation_status | ENUM منطقي | نعم | صالح، تحذير، أو خطأ |
| matched_employee_id | UUID FK employees | لا | الموظف المطابق وقت المعاينة، `PROTECT` |
| created_at | TIMESTAMPTZ | نعم | وقت التسجيل |

**القيود والفهارس:** Unique على `(batch_id, row_number)`، وفهارس على `(batch_id, validation_status)` و`national_id_hash`.

### 5.10 جدول `employee_import_errors`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| batch_id | UUID FK employee_import_batches | نعم | `PROTECT` |
| row_id | UUID FK employee_import_rows | لا | `PROTECT`؛ Null لأخطاء الملف العامة |
| error_code | VARCHAR(80) | نعم | رمز ثابت |
| severity | ENUM منطقي | نعم | تحذير أو خطأ مانع |
| field_name | VARCHAR(100) | لا | اسم العمود المنطقي |
| message_ar | VARCHAR(500) | نعم | رسالة عربية آمنة |
| masked_value | VARCHAR(255) | لا | قيمة مقنعة فقط |
| created_at | TIMESTAMPTZ | نعم | وقت التسجيل |

**القيود والفهارس:** فهرس على `(batch_id, severity)` وعلى `error_code`، ولا تخزن قيمة حساسة كاملة.

## 6. الحقول التفصيلية: سياسات الدوام والجداول

### 6.1 جدول `work_policies`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| code | VARCHAR(50) | نعم | رمز عائلة السياسة |
| version | INTEGER | نعم | رقم الإصدار |
| name_ar | VARCHAR(200) | نعم | الاسم العربي |
| effective_from | DATE | نعم | بداية النفاذ |
| effective_to | DATE | لا | نهاية النفاذ |
| grace_in_minutes | SMALLINT | نعم | سماح الدخول |
| grace_out_minutes | SMALLINT | نعم | سماح الخروج |
| minimum_work_minutes | SMALLINT | لا | الحد الأدنى لليوم |
| rules_json | JSONB | نعم | قواعد إضافية مضبوطة بمخطط إصدار |
| status | ENUM منطقي | نعم | مسودة، نافذة، منتهية، مؤرشفة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `(code, version)`، وCheck للقيم غير السالبة والفترة، وفهرس على `(code, status, effective_from, effective_to)`.

### 6.2 جدول `shifts`

يدعم الإصدار الأول المناوبات العابرة لمنتصف الليل. يحدد `crosses_midnight=True` أن `end_time` يقع في اليوم التالي، ويطبق إسناد يوم العمل وحواف الالتقاط داخل Services وفق هذا القرار.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| policy_id | UUID FK work_policies | نعم | `PROTECT` |
| code | VARCHAR(50) | نعم | رمز المناوبة |
| name_ar | VARCHAR(150) | نعم | الاسم العربي |
| start_time | TIME | نعم | وقت البداية المحلي |
| end_time | TIME | نعم | وقت النهاية المحلي |
| crosses_midnight | BOOLEAN | نعم | هل تعبر منتصف الليل |
| check_in_window_before | SMALLINT | نعم | دقائق قبل البداية |
| check_in_window_after | SMALLINT | نعم | دقائق بعد البداية |
| check_out_window_before | SMALLINT | نعم | دقائق قبل النهاية |
| check_out_window_after | SMALLINT | نعم | دقائق بعد النهاية |
| work_days_mask | VARCHAR(7) | نعم | أيام الأسبوع وفق صيغة موثقة |
| is_active | BOOLEAN | نعم | الحالة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `(policy_id, code)`، وCheck للدقائق و`work_days_mask`، وفهرس على `(policy_id, is_active)`.

### 6.3 جدول `employee_shift_assignments`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| employee_id | UUID FK employees | نعم | `PROTECT` |
| shift_id | UUID FK shifts | نعم | `PROTECT` |
| valid_from | DATE | نعم | بداية النفاذ |
| valid_to | DATE | لا | نهاية النفاذ |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `(employee_id, shift_id, valid_from)`، وCheck للفترة، ومنع تداخل مناوبتين أساسيتين عبر Exclusion Constraint أو Service. فهرس على `(employee_id, valid_from, valid_to)`.

### 6.4 جدول `holiday_calendars`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| code | VARCHAR(50) | نعم | رمز التقويم |
| name_ar | VARCHAR(150) | نعم | الاسم العربي |
| department_id | UUID FK departments | لا | نطاق خاص، `PROTECT` |
| location_id | UUID FK locations | لا | نطاق موقع، `PROTECT` |
| is_default | BOOLEAN | نعم | التقويم الافتراضي |
| is_active | BOOLEAN | نعم | الحالة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `code`، وCheck يمنع تحديد قسم وموقع معًا إن لم تعتمد الجهة ذلك، وفهارس على `department_id` و`location_id`.

### 6.5 جدول `holidays`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| calendar_id | UUID FK holiday_calendars | نعم | `PROTECT` |
| holiday_date | DATE | نعم | التاريخ |
| name_ar | VARCHAR(200) | نعم | اسم المناسبة |
| holiday_type | ENUM منطقي | نعم | رسمي، داخلي، استثنائي |
| is_paid | BOOLEAN | نعم | معلومة سياسة فقط في V1 |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `(calendar_id, holiday_date)`، وفهرس على `holiday_date`.

## 7. الحقول التفصيلية: الاستيراد والحضور

### 7.1 جدول `operational_periods` (OperationalPeriod)

الاسم المعتمد نهائيًا للكيان هو **OperationalPeriod** واسم الجدول `operational_periods`. ينظم الفترات التشغيلية المعتمدة مركزيًا دون تكرار حقل مثل `working_year` في كل جدول. تظل `period_start` و`period_end` مصدر الحقيقة الزمني داخل الكيان وفي العمليات المرتبطة به.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي UUID v4 |
| code | VARCHAR(50) | نعم | رمز فريد مثل `OP-2026-01` دون افتراض أنه سنة فقط |
| name_ar | VARCHAR(150) | نعم | اسم الفترة بالعربية |
| period_type | ENUM منطقي | نعم | تشغيلية، مالية، أو تقارير |
| period_start | DATE | نعم | بداية الفترة الأساسية |
| period_end | DATE | نعم | نهاية الفترة الأساسية |
| status | ENUM منطقي | نعم | مخطط، مفتوح، مقفل، مغلق |
| is_active | BOOLEAN | نعم | يسمح باستخدام الفترة في عمليات جديدة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `code`، وUnique على `(period_type, period_start, period_end)`، وCheck أن `period_end >= period_start`. فهرس مركب على `(status, period_start, period_end)` لخدمة اختيار الفترات. لا يضاف `working_year` إلى أي جدول؛ يمكن اشتقاق السنة للعرض أو التقرير من التاريخ، أما الفترات غير السنوية فتمثل بهذا الكيان.

**متى يستخدم:**

- يربط بعملية الاستيراد عندما يتبع الملف فترة تشغيلية معتمدة.
- يربط بتشغيل الاحتساب لتجميع التشغيلات والتحقق من حدودها.
- يربط بقفل الفترة ليكون القفل قابلًا للتقرير والمراجعة.
- يربط بطلب تصدير التقرير عندما يكون التقرير مبنيًا على فترة رسمية.
- تبقى العلاقة اختيارية للعمليات المخصصة أو الانتقالية، لكن `period_start` و`period_end` يظلان إلزاميين في الجداول التشغيلية لضمان لقطة زمنية مستقرة حتى لو تغير اسم الفترة أو حالتها.

### 7.2 جدول `import_batches`

يمثل ملف Excel واحدًا. لا يحذف الملف أو رأس العملية أثناء مدة الاحتفاظ التشغيلية.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| original_filename | VARCHAR(255) | نعم | اسم منقى للعرض، دون سجل مدني |
| storage_key | VARCHAR(500) | نعم | مسار عشوائي آمن خارج المسار التنفيذي |
| file_sha256 | CHAR(64) | نعم | بصمة منع تكرار الملف |
| file_size_bytes | BIGINT | نعم | الحجم |
| mime_type | VARCHAR(100) | نعم | النوع الفعلي المتحقق منه |
| template_version | VARCHAR(30) | نعم | إصدار قالب Excel |
| source_name | VARCHAR(100) | نعم | مصدر الملف |
| operational_period_id | UUID FK operational_periods | لا | الفترة الرسمية عند انطباقها، `PROTECT` |
| period_start | DATE | نعم | بداية الفترة |
| period_end | DATE | نعم | نهاية الفترة |
| status | ENUM منطقي | نعم | مرفوع، يتحقق، به أخطاء، جاهز، معتمد، فشل، ملغى |
| total_rows | INTEGER | نعم | إجمالي الصفوف |
| accepted_rows | INTEGER | نعم | المقبولة |
| rejected_rows | INTEGER | نعم | المرفوضة |
| warning_rows | INTEGER | نعم | التحذيرات |
| employee_count | INTEGER | نعم | عدد مجموعات الموظفين المستخرجة من التقرير |
| daily_record_count | INTEGER | نعم | عدد صفوف أيام الدوام ذات التاريخ الصالح |
| matched_rows | INTEGER | نعم | عدد سجلات الأيام المطابقة بموظف موجود |
| unmatched_rows | INTEGER | نعم | عدد سجلات الأيام غير المطابقة بموظف موجود |
| ignored_rows | INTEGER | نعم | عدد صفوف عنوان الفترة والرؤوس والصفوف الفارغة المتجاهلة، دون صفوف المجموع |
| summary_rows | INTEGER | نعم | عدد صفوف «المجموع» المتجاهلة |
| error_rows | INTEGER | نعم | عدد الصفوف ذات الأخطاء المانعة |
| distinct_location_count | INTEGER | نعم | عدد قيم مواقع المصدر المختلفة بعد التطبيع |
| uploaded_by | UUID FK users | لا | `SET NULL` مع الاحتفاظ بلقطة الفاعل في التدقيق |
| approved_by | UUID FK users | لا | `SET NULL` |
| approved_at | TIMESTAMPTZ | لا | وقت اعتماد الاستيراد |
| processing_started_at | TIMESTAMPTZ | لا | بداية المعالجة |
| processing_finished_at | TIMESTAMPTZ | لا | نهاية المعالجة |
| failure_summary | TEXT | لا | رسالة آمنة غير حساسة |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |

**القيود والفهارس:** Unique على `file_sha256` في V1، وUnique على `storage_key`، وCheck للفترة والعدادات والحجم. فهارس على `(status, created_at)` و`(operational_period_id, period_start, period_end)` عند وجود الفترة. لا يضاف فهرس منفصل على `uploaded_by` قبل إثبات احتياجه؛ يمكن تغطية سجل المستخدم بفهرس مركب لاحقًا وفق القياس.

### 7.3 جدول `import_rows`

يحفظ الصف الأصلي بصورة مشفرة وغير قابلة للتعديل. لا يحتوي `updated_at`.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| batch_id | UUID FK import_batches | نعم | `PROTECT` للحفاظ على المصدر |
| row_number | INTEGER | نعم | رقم الصف في الملف |
| raw_payload_encrypted | BYTEA | نعم | القيم الأصلية مشفرة كما وردت |
| raw_payload_sha256 | CHAR(64) | نعم | بصمة الصف الأصلية |
| schema_version | VARCHAR(30) | نعم | مخطط تفسير الحمولة |
| validation_status | ENUM منطقي | نعم | مقبول، تحذير، مرفوض |
| created_at | TIMESTAMPTZ | نعم | وقت الإدخال |

**القيود والفهارس:** Unique على `(batch_id, row_number)`، وفهرس على `(batch_id, validation_status)` وعلى `raw_payload_sha256`. يمنع UPDATE وDELETE تشغيليًا عبر Service وصلاحيات قاعدة البيانات في الإنتاج.

### 7.4 جدول `import_errors`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| batch_id | UUID FK import_batches | نعم | `PROTECT` |
| row_id | UUID FK import_rows | لا | `PROTECT`؛ Null لأخطاء الملف العامة |
| error_code | VARCHAR(80) | نعم | رمز ثابت قابل للترجمة |
| severity | ENUM منطقي | نعم | تحذير أو خطأ |
| field_name | VARCHAR(100) | لا | اسم العمود المنطقي |
| message_ar | VARCHAR(500) | نعم | رسالة عربية آمنة |
| masked_value | VARCHAR(255) | لا | قيمة مقنعة فقط، دون سجل مدني كامل |
| created_at | TIMESTAMPTZ | نعم | وقت التسجيل |

**القيود والفهارس:** Unique على `(batch_id, row_id, error_code, field_name)` مع معالجة Null وفق PostgreSQL، وفهرس على `(batch_id, severity)` وعلى `error_code`.

### 7.5 جدول `raw_attendance_records`

سجل الحضور الأصلي المفسر من الصف، دون تعديل بيانات المصدر. بما أن كل صف Excel يمثل سجل حضور يومي لموظف، يحتوي السجل على وقتي الدخول والخروج كما وردا إن توفرا.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| import_row_id | UUID FK import_rows | نعم | OneToOne، `PROTECT` |
| employee_id | UUID FK employees | لا | نتيجة المطابقة، `PROTECT` |
| national_id_hash | CHAR(64) | لا | بصمة السجل المدني المستخدم للمطابقة |
| attendance_date | DATE | لا | التاريخ المفسر |
| source_check_in_at | TIMESTAMPTZ | لا | دخول المصدر |
| source_check_out_at | TIMESTAMPTZ | لا | خروج المصدر |
| source_check_in_location | VARCHAR(255) | لا | مكان الحضور كما ورد بعد التطبيع |
| source_check_out_location | VARCHAR(255) | لا | مكان الانصراف كما ورد بعد التطبيع |
| matched_location_id | UUID FK locations | لا | الموقع المطابق لمكان الحضور عند إمكان المطابقة، `PROTECT` |
| source_status | VARCHAR(100) | لا | حالة المصدر كما وردت بعد التنقية |
| source_scheduled_duration | INTERVAL | لا | ساعات الدوام الواردة من المصدر |
| source_actual_work_duration | INTERVAL | لا | ساعات الدوام الفعلي الواردة من المصدر |
| source_early_departure_duration | INTERVAL | لا | الانصراف المبكر الوارد من المصدر |
| source_shortfall_duration | INTERVAL | لا | النقص في الدوام الوارد من المصدر |
| source_early_arrival_duration | INTERVAL | لا | الحضور المبكر الوارد من المصدر |
| record_fingerprint | CHAR(64) | نعم | بصمة مستقرة لمنع تكرار السجل عبر الملفات |
| match_status | ENUM منطقي | نعم | مطابق، غير مطابق، ملتبس، مرفوض |
| match_method | ENUM منطقي | لا | سجل مدني آلي أو مراجعة معتمدة |
| matched_at | TIMESTAMPTZ | لا | وقت المطابقة |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |

**القيود والفهارس:** Unique على `import_row_id` وUnique على `record_fingerprint`. فهارس على `national_id_hash`، و`(employee_id, attendance_date)`، و`(match_status, attendance_date)`، و`matched_location_id`. أي تغيير في الربط يتم بسجل معالجة مستقل أو بإصدار ربط موثق؛ لا تعدل قيم المصدر.

### 7.6 جدول `calculation_runs`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| run_type | ENUM منطقي | نعم | أولي، إعادة احتساب، تطبيق معالجة |
| operational_period_id | UUID FK operational_periods | لا | الفترة الرسمية عند انطباقها، `PROTECT` |
| period_start | DATE | نعم | بداية الفترة |
| period_end | DATE | نعم | نهاية الفترة |
| department_id | UUID FK departments | لا | نطاق التشغيل، `PROTECT` |
| status | ENUM منطقي | نعم | مجدول، يعمل، نجح، فشل، ألغي |
| rules_version | VARCHAR(50) | نعم | إصدار محرك القواعد |
| parameters_json | JSONB | نعم | معلمات غير حساسة |
| reason | TEXT | لا | سبب إعادة الاحتساب |
| requested_by | UUID FK users | لا | `SET NULL` |
| started_at | TIMESTAMPTZ | لا | البداية |
| finished_at | TIMESTAMPTZ | لا | النهاية |
| failure_summary | TEXT | لا | ملخص آمن |
| created_at | TIMESTAMPTZ | نعم | وقت الطلب |

**القيود والفهارس:** Check للفترة، وفهارس على `(status, created_at)` و`(operational_period_id, period_start, period_end)` و`(department_id, period_start, period_end)`. لا يضاف فهرس منفرد مكرر على `department_id` لأن الفهرس المركب يبدأ به.

### 7.7 جدول `daily_attendance_results`

نتيجة مشتقة قابلة للإصدار، وليست بديلًا عن السجل الأصلي.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| employee_id | UUID FK employees | نعم | `PROTECT` |
| attendance_date | DATE | نعم | يوم العمل |
| version | INTEGER | نعم | إصدار النتيجة لليوم |
| is_current | BOOLEAN | نعم | الإصدار الحالي فقط |
| calculation_run_id | UUID FK calculation_runs | نعم | `PROTECT` |
| policy_id | UUID FK work_policies | لا | `PROTECT` |
| shift_id | UUID FK shifts | لا | `PROTECT` |
| department_id | UUID FK departments | نعم | لقطة القسم النافذ، `PROTECT` |
| primary_location_id | UUID FK locations | لا | الموقع الأساسي النافذ، `PROTECT` |
| actual_location_id | UUID FK locations | لا | موقع التوقيع المطابق، `PROTECT` |
| first_check_in_at | TIMESTAMPTZ | لا | أول دخول معتمد حسابيًا |
| last_check_out_at | TIMESTAMPTZ | لا | آخر خروج معتمد حسابيًا |
| scheduled_minutes | INTEGER | نعم | الدقائق المجدولة |
| worked_minutes | INTEGER | نعم | الدقائق المحتسبة |
| late_minutes | INTEGER | نعم | التأخر |
| early_leave_minutes | INTEGER | نعم | الانصراف المبكر |
| overtime_minutes | INTEGER | نعم | إضافي مبدئي دون أثر مالي |
| attendance_status | ENUM منطقي | نعم | حاضر، غائب، عطلة، ناقص، مستثنى |
| location_match_status | ENUM منطقي | نعم | مطابق، مختلف، غير معروف، غير مطلوب |
| calculation_snapshot | JSONB | نعم | لقطة تفسيرية للقواعد والمدخلات غير الحساسة |
| superseded_at | TIMESTAMPTZ | لا | وقت استبدال الإصدار |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |

**القيود والفهارس:** Unique على `(employee_id, attendance_date, version)`، وPartial Unique في PostgreSQL على `(employee_id, attendance_date) WHERE is_current = true`. Check للدقائق غير السالبة ولـ`version > 0`. يستخدم فهرس جزئي على `(department_id, attendance_date) WHERE is_current = true` للنتائج الحالية، وفهرس على `(attendance_status, attendance_date)`. لا يكرر فهرس `(employee_id, attendance_date)` لأن القيدين الفريدين يغطيان بدايته، ولا يفهرس `location_match_status` منفردًا قبل إثبات انتقائيته.

### 7.8 جدول `daily_attendance_sources`

جدول وسيط ManyToMany يوضح السجلات الخام التي أسهمت في كل نتيجة يومية.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| daily_result_id | UUID FK daily_attendance_results | نعم | `PROTECT` |
| raw_record_id | UUID FK raw_attendance_records | نعم | `PROTECT` |
| source_role | ENUM منطقي | نعم | أساسي، داعم، مستبعد مع السبب |
| exclusion_reason | VARCHAR(250) | لا | سبب الاستبعاد الحسابي |
| created_at | TIMESTAMPTZ | نعم | وقت الربط |

**القيود والفهارس:** Unique على `(daily_result_id, raw_record_id)`، وفهرس على `(raw_record_id, daily_result_id)`.

### 7.9 جدول `administrative_adjustments`

يحفظ المعالجة الإدارية مستقلة عن البيانات الخام. لا يسمح بتغيير سجل Excel الأصلي.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| employee_id | UUID FK employees | نعم | `PROTECT` |
| attendance_date | DATE | نعم | اليوم المتأثر |
| adjustment_type | ENUM منطقي | نعم | تصحيح ربط، استثناء، إضافة دخول/خروج إداري، إلغاء أثر |
| requested_values | JSONB | نعم | القيم المقترحة دون تعديل المصدر |
| reason | TEXT | نعم | السبب الإداري |
| reference_number | VARCHAR(100) | لا | مرجع القرار |
| status | ENUM منطقي | نعم | مسودة، تحت الاعتماد، معتمد، مرفوض، مطبق، ملغى |
| resolution_request_id | UUID FK resolution_requests | لا | OneToOne منطقي، `PROTECT` |
| applied_in_run_id | UUID FK calculation_runs | لا | التشغيل الذي طبق الأثر، `PROTECT` |
| applied_at | TIMESTAMPTZ | لا | وقت التطبيق |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `resolution_request_id` عندما لا يكون Null، وفهارس على `(employee_id, attendance_date)` و`(status, created_at)` و`applied_in_run_id`.

### 7.10 جدول `period_locks`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| operational_period_id | UUID FK operational_periods | لا | الفترة الرسمية التي يغطيها القفل، `PROTECT` |
| department_id | UUID FK departments | لا | Null يعني النطاق العام، `PROTECT` |
| period_start | DATE | نعم | البداية |
| period_end | DATE | نعم | النهاية |
| status | ENUM منطقي | نعم | مفتوح، مقفل، أعيد فتحه |
| lock_reason | TEXT | نعم | سبب القفل |
| locked_by | UUID FK users | لا | `SET NULL` |
| locked_at | TIMESTAMPTZ | لا | وقت القفل |
| reopened_by | UUID FK users | لا | `SET NULL` |
| reopened_at | TIMESTAMPTZ | لا | وقت إعادة الفتح |
| reopen_reason | TEXT | لا | إلزامي عند الفتح |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |

**القيود والفهارس:** يستخدم Partial Unique على `(period_start, period_end) WHERE department_id IS NULL` للقفل العام، وPartial Unique على `(department_id, period_start, period_end) WHERE department_id IS NOT NULL` لقفل القسم، حتى لا تسمح Null بأقفال عامة مكررة. يضاف Check للفترة وتوافق الحالة مع تواريخ القفل، وفهرس على `(operational_period_id, department_id, status)` لدعم القفل الرسمي. يغطي القيد الثاني البحث المعتاد بـ`department_id + period_start + period_end`، لذلك لا يكرر بفهرس مطابق.

## 8. الحقول التفصيلية: المخالفات والمعالجات والاعتمادات

### 8.1 جدول `violation_types`

جدول مرجعي بسيط؛ يستخدم `is_active` لإيقاف النوع عن الإنشاء المستقبلي مع بقاء المخالفات السابقة مرتبطة به.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| code | VARCHAR(50) | نعم | رمز النوع |
| name_ar | VARCHAR(150) | نعم | الاسم العربي |
| description_ar | TEXT | لا | الوصف |
| severity | SMALLINT | نعم | مستوى الشدة |
| rule_code | VARCHAR(100) | نعم | قاعدة الاكتشاف في Services |
| requires_approval | BOOLEAN | نعم | هل المعالجة تحتاج اعتمادًا |
| resolution_deadline_days | SMALLINT | لا | مهلة التقديم |
| is_active | BOOLEAN | نعم | الحالة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `code`، وCheck للشدة والمهلة، وفهرس على `(is_active, severity)`.

### 8.2 جدول `violations`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| employee_id | UUID FK employees | نعم | `PROTECT` |
| daily_result_id | UUID FK daily_attendance_results | نعم | `PROTECT` |
| violation_type_id | UUID FK violation_types | نعم | `PROTECT` |
| department_id | UUID FK departments | نعم | لقطة نطاق الموظف، `PROTECT` |
| violation_date | DATE | نعم | تاريخ المخالفة |
| measured_value | DECIMAL(12,2) | لا | دقائق أو قيمة حسب النوع |
| unit | VARCHAR(30) | لا | وحدة القياس |
| status | ENUM منطقي | نعم | مفتوحة، تحت المعالجة، مثبتة، معالجة، ملغاة، مستبدلة |
| rule_snapshot | JSONB | نعم | تفسير القاعدة والإصدار |
| superseded_by_id | UUID FK violations | لا | الإصدار البديل، `PROTECT` |
| detected_at | TIMESTAMPTZ | نعم | وقت الاكتشاف |
| closed_at | TIMESTAMPTZ | لا | وقت الإغلاق |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |

**القيود والفهارس:** Unique على `(daily_result_id, violation_type_id)`، وCheck يمنع `superseded_by_id = id`. فهارس على `(employee_id, violation_date)`، و`(department_id, status, violation_date)`، و`violation_type_id`.

### 8.3 جدول `resolution_requests`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| request_number | VARCHAR(30) | نعم | رقم عرض غير حساس وفريد |
| violation_id | UUID FK violations | نعم | `PROTECT` |
| employee_id | UUID FK employees | نعم | صاحب الطلب، `PROTECT` |
| submitted_by | UUID FK users | لا | مقدم الطلب، `SET NULL` |
| request_type | ENUM منطقي | نعم | اعتراض، تبرير، طلب تصحيح |
| reason | TEXT | نعم | المبرر |
| requested_action | ENUM منطقي | نعم | إلغاء، تعديل، إضافة استثناء، مراجعة |
| status | ENUM منطقي | نعم | مسودة، مقدم، تحت المراجعة، معاد، معتمد، مرفوض، ملغى، مطبق |
| submitted_at | TIMESTAMPTZ | لا | وقت التقديم |
| due_at | TIMESTAMPTZ | لا | مهلة المعالجة |
| resolved_at | TIMESTAMPTZ | لا | وقت القرار النهائي |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `request_number`. يوصى بـ Partial Unique يمنع أكثر من طلب فعال لنفس `(violation_id, employee_id)` للحالات المفتوحة. فهارس على `(employee_id, status, created_at)`، و`(status, due_at)`، و`violation_id`.

### 8.4 جدول `resolution_attachments`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| request_id | UUID FK resolution_requests | نعم | `PROTECT` |
| original_filename | VARCHAR(255) | نعم | اسم منقى |
| storage_key | VARCHAR(500) | نعم | اسم عشوائي دون سجل مدني |
| file_sha256 | CHAR(64) | نعم | بصمة الملف |
| mime_type | VARCHAR(100) | نعم | النوع المتحقق منه |
| size_bytes | BIGINT | نعم | الحجم |
| scan_status | ENUM منطقي | نعم | معلق، سليم، مرفوض |
| uploaded_by | UUID FK users | لا | `SET NULL` |
| created_at | TIMESTAMPTZ | نعم | وقت الرفع |
| archived_at | TIMESTAMPTZ | لا | إخفاء منطقي بعد انتهاء الحاجة |

**القيود والفهارس:** Unique على `storage_key`، وUnique على `(request_id, file_sha256)`، وCheck للحجم، وفهرس على `(request_id, scan_status)`.

### 8.5 جدول `approval_workflows`

قالب مسار قابل للإصدار. يدعم V1 **مرحلة اعتماد واحدة فقط** لكل مسار فعال، ولا تعدل النسخة المستخدمة في طلبات جارية. يحتفظ التصميم بجدولي القالب والخطوات وبحقل `step_order` حتى يمكن دعم مراحل متعددة مستقبلًا دون إعادة تصميم البيانات، لكن Service في V1 يرفض تفعيل مسار يحتوي أكثر من خطوة.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| code | VARCHAR(50) | نعم | رمز عائلة المسار |
| version | INTEGER | نعم | الإصدار |
| name_ar | VARCHAR(150) | نعم | الاسم العربي |
| target_type | ENUM منطقي | نعم | معالجة مخالفة في V1 |
| effective_from | DATE | نعم | بداية النفاذ |
| effective_to | DATE | لا | نهاية النفاذ |
| status | ENUM منطقي | نعم | مسودة، نافذ، منتهي، مؤرشف |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `(code, version)`، وCheck للفترة والإصدار، وفهرس على `(target_type, status, effective_from)`.

### 8.6 جدول `approval_workflow_steps`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| workflow_id | UUID FK approval_workflows | نعم | `PROTECT` |
| step_order | SMALLINT | نعم | ترتيب المرحلة |
| name_ar | VARCHAR(150) | نعم | اسم المرحلة |
| approver_role_id | UUID FK roles | نعم | الدور المطلوب، `PROTECT` |
| scope_rule | ENUM منطقي | نعم | قسم الموظف، المدير المباشر، موارد بشرية، شامل |
| decision_mode | ENUM منطقي | نعم | موافقة مستخدم واحد في V1 |
| due_hours | INTEGER | لا | المهلة |
| allow_return | BOOLEAN | نعم | السماح بالإعادة للاستكمال |
| is_active | BOOLEAN | نعم | الحالة |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `(workflow_id, step_order)`، وCheck للترتيب والمهلة، وفهرس على `(workflow_id, is_active)`.

### 8.7 جدول `approval_instances`

نسخة تشغيلية من المسار مرتبطة بطلب معالجة واحد.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| request_id | UUID FK resolution_requests | نعم | OneToOne، `PROTECT` |
| workflow_id | UUID FK approval_workflows | نعم | `PROTECT` |
| status | ENUM منطقي | نعم | قيد التنفيذ، معتمد، مرفوض، ملغى |
| current_step_order | SMALLINT | لا | المرحلة الحالية |
| started_at | TIMESTAMPTZ | نعم | البداية |
| completed_at | TIMESTAMPTZ | لا | النهاية |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |

**القيود والفهارس:** Unique على `request_id`، وفهارس على `(status, started_at)` و`workflow_id`.

### 8.8 جدول `approval_step_instances`

لقطة تشغيلية لكل مرحلة تمنع تغير الطلب الجاري عند تعديل القالب.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| approval_instance_id | UUID FK approval_instances | نعم | `PROTECT` |
| workflow_step_id | UUID FK approval_workflow_steps | نعم | `PROTECT` |
| step_order | SMALLINT | نعم | ترتيب مثبت |
| name_ar_snapshot | VARCHAR(150) | نعم | اسم المرحلة وقت الإنشاء |
| approver_role_id | UUID FK roles | نعم | الدور المثبت، `PROTECT` |
| assigned_user_id | UUID FK users | لا | المعتمد المعين، `SET NULL` |
| status | ENUM منطقي | نعم | منتظر، نشط، معتمد، مرفوض، معاد، متجاوز |
| opened_at | TIMESTAMPTZ | لا | وقت فتح المرحلة |
| due_at | TIMESTAMPTZ | لا | المهلة |
| completed_at | TIMESTAMPTZ | لا | وقت الإكمال |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |

**القيود والفهارس:** Unique على `(approval_instance_id, step_order)`، وفهارس على `(assigned_user_id, status, due_at)` وعلى `(approver_role_id, status)`.

### 8.9 جدول `approval_decisions`

سجل غير قابل للتعديل. أي تصحيح ينشئ قرارًا جديدًا مرتبطًا بالسابق.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| step_instance_id | UUID FK approval_step_instances | نعم | `PROTECT` |
| decided_by | UUID FK users | لا | `SET NULL` مع لقطة هوية في Audit Log |
| decision | ENUM منطقي | نعم | موافقة، رفض، إعادة، إلغاء |
| comment | TEXT | لا | إلزامي للرفض والإعادة |
| previous_decision_id | UUID FK approval_decisions | لا | قرار مصحح، `PROTECT` |
| decided_at | TIMESTAMPTZ | نعم | وقت القرار |
| request_ip | INET | لا | مصدر القرار عند الحاجة |
| created_at | TIMESTAMPTZ | نعم | يساوي وقت التسجيل تقريبًا |

**القيود والفهارس:** Check للتعليق حسب القرار ولمنع المرجع الذاتي. يحدد Service القرار الفعال ويمنع قرارين نهائيين متعارضين للمرحلة. فهارس على `(step_instance_id, decided_at)` و`(decided_by, decided_at)`.

## 9. الحقول التفصيلية: الخدمات المشتركة

### 9.1 جدول `notifications`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| recipient_user_id | UUID FK users | نعم | `PROTECT` |
| notification_type | VARCHAR(80) | نعم | نوع الحدث |
| title_ar | VARCHAR(200) | نعم | العنوان العربي |
| body_ar | TEXT | نعم | النص العربي دون أسرار |
| related_object_type | VARCHAR(80) | لا | نوع الكيان المرجعي |
| related_object_id | UUID | لا | معرف الكيان دون FK متعدد الأشكال |
| priority | ENUM منطقي | نعم | عادي، مهم، عاجل |
| status | ENUM منطقي | نعم | غير مقروء، مقروء، مؤرشف |
| read_at | TIMESTAMPTZ | لا | وقت القراءة |
| expires_at | TIMESTAMPTZ | لا | انتهاء العرض |
| created_at | TIMESTAMPTZ | نعم | وقت الإنشاء |

**القيود والفهارس:** فهارس على `(recipient_user_id, status, created_at DESC)` وعلى `(related_object_type, related_object_id)`، وCheck لتوافق `read_at` مع الحالة.

### 9.2 جدول `report_exports`

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| report_code | VARCHAR(80) | نعم | نوع التقرير |
| requested_by | UUID FK users | لا | `SET NULL` |
| operational_period_id | UUID FK operational_periods | لا | الفترة الرسمية للتقرير عند انطباقها، `PROTECT` |
| period_start | DATE | نعم | بداية نطاق التقرير |
| period_end | DATE | نعم | نهاية نطاق التقرير |
| department_scope_id | UUID FK departments | لا | النطاق المثبت، `PROTECT` |
| filters_json | JSONB | نعم | مرشحات منقحة دون سجل مدني كامل |
| format | ENUM منطقي | نعم | Excel أو PDF عند اعتماده |
| status | ENUM منطقي | نعم | مطلوب، يعمل، جاهز، فشل، منتهي |
| storage_key | VARCHAR(500) | لا | مسار عشوائي مؤقت |
| file_sha256 | CHAR(64) | لا | بصمة الناتج |
| expires_at | TIMESTAMPTZ | لا | نهاية صلاحية التنزيل |
| completed_at | TIMESTAMPTZ | لا | وقت الإنجاز |
| failure_summary | TEXT | لا | ملخص آمن |
| created_at | TIMESTAMPTZ | نعم | وقت الطلب |

**القيود والفهارس:** Unique على `storage_key` عندما لا يكون Null، وCheck أن `period_end >= period_start`. فهارس على `(requested_by, created_at DESC)` و`(status, created_at)` و`(operational_period_id, period_start, period_end)` و`expires_at`.

### 9.3 جدول `audit_logs`

سجل Append-only لجميع العمليات الحساسة. لا يستخدم Soft Delete ولا يسمح بالتعديل.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| occurred_at | TIMESTAMPTZ | نعم | وقت الحدث |
| actor_user_id | UUID FK users | لا | `SET NULL` |
| actor_username_snapshot | VARCHAR(150) | لا | لقطة اسم المستخدم عند الحدث |
| action | VARCHAR(100) | نعم | رمز العملية |
| module | VARCHAR(50) | نعم | الوحدة |
| object_type | VARCHAR(100) | لا | نوع الكيان |
| object_id | UUID | لا | معرف الكيان دون FK متعدد الأشكال |
| object_repr_masked | VARCHAR(255) | لا | وصف مقنع |
| department_scope_id | UUID FK departments | لا | نطاق الحدث، `PROTECT` |
| before_json | JSONB | لا | قيم قبل التغيير بعد الحجب |
| after_json | JSONB | لا | قيم بعد التغيير بعد الحجب |
| reason | TEXT | لا | سبب العملية |
| outcome | ENUM منطقي | نعم | نجاح، فشل، رفض صلاحية |
| request_id | UUID | لا | معرف تتبع الطلب |
| session_key_hash | CHAR(64) | لا | بصمة الجلسة لا قيمتها |
| ip_address | INET | لا | عنوان المصدر |
| user_agent | VARCHAR(500) | لا | وكيل المستخدم بعد الحد |
| integrity_hash | CHAR(64) | لا | بصمة سلامة اختيارية متسلسلة |

**القيود والفهارس:** فهارس على `(occurred_at DESC)`، و`(actor_user_id, occurred_at DESC)`، و`(object_type, object_id, occurred_at)`، و`(department_scope_id, occurred_at)`، و`(action, outcome, occurred_at)`. يوصى بتقسيم الجدول زمنيًا في PostgreSQL عند نموه. يمنع تسجيل كلمات المرور والأسرار والسجل المدني الكامل في حقول JSON أو النصوص.

### 9.4 جدول `system_settings`

إعدادات تشغيل مرنة غير سرية. الأسرار تحفظ في مدير أسرار خارجي ويخزن هنا مرجعها فقط عند الحاجة.

| الحقل | النوع | الإلزام | الوصف |
|---|---|---:|---|
| id | UUID PK | نعم | المفتاح الأساسي |
| key | VARCHAR(120) | نعم | مفتاح الإعداد |
| name_ar | VARCHAR(200) | نعم | الاسم العربي |
| description_ar | TEXT | لا | الشرح |
| value_type | ENUM منطقي | نعم | نص، رقم، منطق، JSON، مرجع سر |
| value_json | JSONB | لا | القيمة غير السرية |
| secret_reference | VARCHAR(255) | لا | مرجع خارجي فقط، وليس السر |
| is_sensitive | BOOLEAN | نعم | يقيد العرض والتعديل |
| is_editable | BOOLEAN | نعم | يسمح بالتعديل الإداري |
| validation_schema | JSONB | لا | مخطط تحقق |
| created_at / updated_at | TIMESTAMPTZ | نعم | حقول زمنية |
| created_by / updated_by | UUID FK users | لا | `SET NULL` |

**القيود والفهارس:** Unique على `key`، وCheck يضمن وجود واحد فقط من `value_json` أو `secret_reference` وفق `value_type`، وفهرس على `(is_sensitive, is_editable)`.

## 10. ملخص العلاقات

### علاقات OneToOne

- `employees.user_id` ↔ `users.id`: حساب اختياري واحد لكل موظف، وحساب الموظف لا يرتبط بموظفين متعددين.
- `employee_identities.employee_id` ↔ `employees.id`: هوية سجل مدني واحدة فعالة للموظف في V1.
- `raw_attendance_records.import_row_id` ↔ `import_rows.id`: سجل مفسر واحد لكل صف مقبول.
- `approval_instances.request_id` ↔ `resolution_requests.id`: مسار تشغيل واحد لكل طلب.
- `administrative_adjustments.resolution_request_id` ↔ `resolution_requests.id`: معالجة تطبيقية واحدة للطلب المعتمد عند الحاجة.

### علاقات ForeignKey

- القسم علاقة ذاتية عبر `departments.parent_id`.
- الموظف يرتبط تاريخيًا بالقسم والمسمى والمدير عبر `employment_assignments`.
- الموظف يرتبط تاريخيًا بموقعه الأساسي عبر `employee_primary_locations`.
- الموظف يرتبط تاريخيًا بالمناوبة عبر `employee_shift_assignments`.
- الفترة التشغيلية ترتبط اختياريًا بدفعات الاستيراد وتشغيلات الاحتساب وأقفال الفترات وطلبات تصدير التقارير، مع بقاء `period_start` و`period_end` في كل عملية كلقطة زمنية أساسية.
- دفعة الاستيراد تملك صفوفًا وأخطاء، والصف مصدر سجل حضور خام.
- النتيجة اليومية ترتبط بالموظف وتشغيل الاحتساب والسياسة والمناوبة والقسم والموقع.
- المخالفة ترتبط بالنتيجة اليومية والموظف ونوع المخالفة.
- طلب المعالجة يرتبط بالمخالفة والموظف، ثم بمسار الاعتماد وقراراته.
- الإشعار والتقرير والتدقيق ترتبط بالمستخدم مع سياسات حذف حافظة للتاريخ.

### علاقات ManyToMany

- `roles` ↔ `permissions` من خلال `role_permissions`.
- `users` ↔ `roles` من خلال `user_roles` مع فترة نفاذ.
- `users` ↔ `departments` من خلال `user_department_scopes` مع مستوى وصول وفترة نفاذ.
- `daily_attendance_results` ↔ `raw_attendance_records` من خلال `daily_attendance_sources`.

## 11. سياسات `on_delete` وأسبابها

| السياسة | مواضع الاستخدام | السبب |
|---|---|---|
| PROTECT / RESTRICT | الموظف، القسم، الموقع، السياسة، المناوبة، ملفات الاستيراد، النتائج، المخالفات، الاعتمادات | منع حذف أصل تعتمد عليه بيانات تاريخية أو رقابية |
| SET NULL | `created_by` و`updated_by` والفاعل والمعتمد والمستخدم المرتبط بالموظف | إبقاء السجل إذا عطل الحساب أو أخفي مع حفظ لقطة الفاعل في Audit Log |
| CASCADE | لا يستخدم في جداول الأعمال التاريخية في V1 | منع فقد السجلات التابعة ضمنيًا؛ لا يعتمد إلا مستقبلًا لكيان تقني مؤقت لا قيمة له دون أصله وبعد مراجعة صريحة |
| لا توجد FK متعددة الأشكال | `audit_logs.object_id` و`notifications.related_object_id` | تجنب الاعتماد على ContentType والحفاظ على الحدث حتى لو أرشف الكيان |

لا يستخدم `CASCADE` من `import_batches` إلى الصفوف، ولا من الموظف إلى سجلات الحضور أو المخالفات. الحذف العادي للبيانات التاريخية ممنوع، والتطهير النظامي بعد انتهاء مدة الاحتفاظ عملية منفصلة موثقة ومقيدة.

## 12. الحقول التاريخية وسياسة إضافتها

### جداول كاملة التتبع

تستخدم `created_at`, `updated_at`, `created_by`, `updated_by` الجداول القابلة للتعديل إداريًا: `users`, `roles`, `departments`, `locations`, `job_titles`, `employees`, `employee_identities`, الإسنادات، السياسات، المناوبات، التقاويم، `operational_periods`، أنواع المخالفات، قوالب الاعتماد، و`system_settings`.

### جداول أحداث غير قابلة للتعديل

تستخدم `created_at` أو وقت الحدث والفاعل فقط: `import_rows`, `import_errors`, `raw_attendance_records`, `daily_attendance_sources`, `approval_decisions`, `audit_logs`. أي تغيير لاحق ينشئ سجلًا جديدًا بدل تحديث الحدث.

### جداول حالة تشغيلية

تستخدم أوقاتًا دلالية إضافية بدل الاعتماد على `updated_at` وحده، مثل `approved_at`, `started_at`, `completed_at`, `applied_at`, `read_at`, `locked_at` و`reopened_at`.

## 13. Soft Delete والأرشفة

| الفئة | الجداول | السياسة |
|---|---|---|
| مرجعيات بسيطة | roles, permissions, job_titles, locations, violation_types | `is_active` فقط؛ الإيقاف يمنع الاستخدام الجديد مع بقاء المراجع التاريخية، ولا يستخدم `archived_at` |
| حسابات وكيانات أعمال | users, employees, departments | `archived_at` عند الحاجة إلى أرشفة فعلية، مع حالة العمل المناسبة ومنع الحذف العادي |
| قواعد وإسنادات تاريخية | work_policies, shifts, employment_assignments, employee_primary_locations, employee_shift_assignments, holiday_calendars, approval_workflows, operational_periods | تحفظ بتاريخ النفاذ أو الحالة (`valid_to`, `effective_to`, `status`, `is_active`) دون `archived_at` مكرر |
| مرفقات | resolution_attachments | أرشفة منطقية ثم تطهير آمن بعد مدة الاحتفاظ |
| بيانات أصلية وتاريخية | import batches/rows, raw records, daily results, violations, approvals, audit logs | لا Soft Delete للمستخدم؛ تحفظ أو تطهر بسياسة احتفاظ مركزية ومُدققة |
| جداول ربط صلاحيات | role_permissions, user_roles, user_department_scopes | الإلغاء يسجل عبر `revoked_at` أو `valid_to`/`is_active` مع Audit Log؛ لا يحذف تاريخ المنح |

لا تحذف البيانات التاريخية أو التشغيلية حذفًا نهائيًا من خلال وظائف النظام اليومية. أي تطهير فعلي بعد انتهاء مدة الاحتفاظ يكون مهمة إدارية منفصلة، تعتمد مسبقًا، وتنفذ بصورة قابلة للتتبع، وتسجل في `audit_logs`. لا يضاف `archived_at` لمجرد الاحتياط؛ يستخدم فقط عندما تكون هناك حالة أرشفة تاريخية تختلف وظيفيًا عن `is_active` أو حقول النفاذ الزمنية.

## 14. تصميم Custom User Model والصلاحيات

### قرار التنفيذ للدفعة الأولى

يستخدم `User` نهج `AbstractBaseUser` دون `PermissionsMixin`. يوفر النموذج الخصائص الدنيا المتوافقة مع Django Admin، بينما تبقى صلاحيات الأعمال في جداول `roles`, `permissions`, `role_permissions`, `user_roles`, و`user_department_scopes`. تجنب `PermissionsMixin` مقصود لأنه ينشئ علاقات تلقائية مع `auth_group` و`auth_permission` وجداول وسيطة لا تتبع تصميم UUID v4 الخاص بالمشروع، كما يكرر منظومة الصلاحيات المعتمدة. لا ينفذ تقييم الصلاحيات الوظيفية الكامل في الدفعة الأولى؛ يسمح `is_superuser` فقط بتجاوز الإدارة التقنية مؤقتًا إلى حين تنفيذ Service الصلاحيات في مرحلة مستقلة.

1. يكون `users.id` من نوع UUID v4 من أول Migration للمشروع.
2. يكون `username` حقل الدخول الوحيد في V1، مع مقارنة غير حساسة لحالة الأحرف بعد تطبيع مضبوط.
3. تخزن كلمة المرور بتجزئة Django المعتمدة؛ لا تخزن أو تسجل بصورتها الأصلية مطلقًا.
4. يفصل حساب المستخدم عن `employees` بعلاقة OneToOne اختيارية؛ يمكن أن يوجد الموظف ويعالج حضوره كاملًا دون حساب مستخدم.
5. لا ينشأ حساب تلقائيًا عند إنشاء موظف أو استيراده. ينشئ الحساب فقط مستخدم ذو صلاحية إدارية مخصصة، وبعد وجود حاجة وصول معتمدة.
6. قد يوجد حساب إداري لا يرتبط بموظف، وقد يوجد موظف لا يرتبط بحساب، لكن الحساب الواحد لا يرتبط بأكثر من موظف.
7. تعتمد الصلاحية على ثلاثة عناصر مجتمعة: صلاحية الإجراء من الدور، ونطاق القسم، وفترة نفاذ الدور والنطاق.
8. مسؤول القسم يرى موظفًا فقط إذا كان الإسناد الوظيفي الأساسي للموظف نافذًا داخل قسم يقع في نطاقه الفعال.
9. القيمة الافتراضية لـ`include_descendants` هي `False`؛ لا يرى مسؤول القسم الأقسام الفرعية إلا عند تفعيلها صراحة في إسناد نطاقه.
10. يمنع Service الاعتماد الذاتي حتى لو امتلك المستخدم صلاحية الاعتماد ونطاق القسم.
11. يستخدم `is_superuser` للطوارئ والإدارة التقنية المقيدة، ولا يكون بديلًا عن أدوار العمل اليومية.
12. تسجل عمليات إنشاء الحساب وربطه أو فك ربطه بالموظف، ومحاولات الدخول، وتغييرات الأدوار والنطاقات في `audit_logs`.

### ملاحظة Django مهمة

اعتمد القرار المعماري DB-001 أن UUID v4 إلزامي لكل جدول مملوك للمشروع. تستثنى فقط جداول Django الداخلية الجاهزة مثل `django_migrations`, `django_content_type`, `django_admin_log` وجداول الجلسات والصلاحيات الداخلية التي ينشئها الإطار. تبقى هذه الجداول تحت إدارة Django، ولا تستخدم مفاتيحها في منطق الأعمال أو في ERD المملوك للمشروع. لذلك لا يؤثر الاستثناء على الهوية أو الصلاحيات الوظيفية أو قابلية التكامل، ولا يستلزم إعادة بناء مكونات Django الداخلية.

## 15. تصميم الاستيراد وعدم قابلية تعديل الأصل

### قالب تقرير الحضور الأسبوعي الرسمي V1

القالب الرسمي للحضور الأسبوعي في V1 هو **تقرير تجميعي متكرر لكل موظف**، وليس جدولًا مسطحًا يفترض تكرار هوية الموظف في كل صف. يتكون الشيت من عنوان للفترة في أعلاه، ثم صف رؤوس الأعمدة، ثم مجموعات موظفين. يظهر السجل المدني والاسم والمسمى الوظيفي في أول صف من مجموعة الموظف، وقد تكون هذه الخلايا في الأيام التالية فارغة أو مدمجة. يمثل كل صف داخل المجموعة يوم دوام واحدًا، ويتبع المجموعة عادة صف «المجموع»، وقد توجد صفوف فارغة بين المجموعات.

الأعمدة الأربعة عشر المعتمدة للقالب، بأسمائها المنطقية العربية، هي:

1. السجل المدني.
2. الاسم.
3. المسمى الوظيفي.
4. التاريخ.
5. حالة التحضير.
6. ساعات الدوام.
7. وقت الحضور.
8. مكان الحضور.
9. توقيت الانصراف.
10. مكان الانصراف.
11. ساعات الدوام الفعلي.
12. انصراف مبكر.
13. النقص في الدوام.
14. حضور مبكر.

قواعد تفسير القالب الرسمي:

1. يستخرج Parser عنوان الفترة ولا يعامله كسجل يومي، ويتجاهل صف رؤوس الأعمدة وصفوف «المجموع» والصفوف الفارغة.
2. يحتفظ Parser بآخر سجل مدني صالح واسم ومسمى وظيفي معروفين داخل مجموعة الموظف، ويطبق هذا السياق على صفوف الأيام التابعة حتى يظهر سجل مدني صالح جديد يبدأ مجموعة جديدة. لا يفترض أن الخلايا المدمجة أو الفارغة تكرر بيانات الموظف.
3. ينشأ سجل مرشح مستقل لكل صف يحمل تاريخ حضور صالحًا. الصف الناقص أو ذو التاريخ أو الوقت غير الصالح يحفظ كصف استيراد مع خطئه الآمن، ولا يتحول إلى سجل حضور مقبول.
4. تطبع الأرقام العربية والفارسية إلى أرقام لاتينية، وتطبع التواريخ والأوقات والمدد، وتحول الشرطة `-` والفراغات إلى Null قبل المطابقة والبصمة.
5. تستخدم المطابقة HMAC-SHA256 للسجل المدني المطبع فقط، بمقارنة `national_id_hash` مع `employee_identities.national_id_hash`. لا تستخدم الأسماء أو المسميات للمطابقة، ولا ينشأ موظف جديد من ملف الحضور. يظهر الموظف غير الموجود كسجل غير مطابق في مركز الأخطاء دون عرض السجل المدني كاملًا.
6. تحفظ الحمولة الأصلية لكل صف مرشح في `import_rows.raw_payload_encrypted` بصورة مشفرة وغير قابلة للتعديل، وتبقى القيم المفسرة في `raw_attendance_records` غير قابلة لإعادة كتابة الأصل.
7. يمنع تكرار الملف بقيد فريد على SHA-256 في `file_sha256`. وتكون `record_fingerprint` بصمة SHA-256 مستقرة ذات إصدار، مبنية من تمثيل معياري يتضمن `employee_id` و`attendance_date` ووقت الحضور ووقت الانصراف ومكاني الحضور والانصراف بعد التطبيع، مع علامات Null ثابتة؛ يمنع القيد الفريد تكرار سجل اليوم عبر الملفات.
8. تحفظ «حالة التحضير» في `source_status` كقيمة مصدر فقط، ولا تعتمد كقرار حضور أو غياب أو مخالفة نهائي.
9. تعرض المعاينة الفترة المستخرجة، وعدد الموظفين، وسجلات الأيام، والمطابقين وغير المطابقين، والصفوف المتجاهلة، وصفوف المجموع، والأخطاء والتحذيرات، وعدد مواقع المصدر المختلفة.
10. يتم اعتماد الدفعة وإدخال السجلات المقبولة داخل `transaction.atomic` واحدة، مع قفل الدفعة والتحقق من حالتها والقيود الفريدة. تكرار طلب الاعتماد يعيد النتيجة المعتمدة دون إدخال جديد، وأي فشل يعيد الدفعة كاملة دون أثر جزئي.
11. لا يعد اختلاف موقع التوقيع عن الموقع الأساسي مخالفة تلقائية؛ يبقى حالة تحليلية **Location Mismatch** حتى تطبق قاعدة نظام معتمدة لاحقًا.
12. تؤجل الحسابات النهائية للحضور والغياب والتأخر والانصراف المبكر وساعات العمل والنقص والحضور المبكر واختلاف الموقع إلى محرك النظام و`DailyAttendanceResult` في مرحلتهما المخصصة. لا ينشئ مستورد V1 مخالفات أو تقارير نهائية.

### مبادئ الاستيراد العامة

1. `import_batches` يحفظ بصمة الملف ومصدره وفترته وحالته.
2. `import_rows` يحفظ الصف الأصلي مشفرًا، وبصمة مستقلة، ولا يسمح بتعديله.
3. `import_errors` يحفظ الأخطاء بصورة منفصلة وقابلة للعرض بالعربية دون كشف السجل المدني.
4. `raw_attendance_records` يحفظ القيم المفسرة والمطابقة إلى الموظف، لكنه لا يستبدل الحمولة الأصلية.
5. منع تكرار الملف يتم بـ`file_sha256`، ومنع تكرار السجل عبر الملفات يتم بـ`record_fingerprint`.
6. المطابقة الآلية تستخدم `national_id_hash` مع `employee_identities.national_id_hash`، وليس الاسم أو الرقم الوظيفي.
7. أي تصحيح إداري يحفظ في `administrative_adjustments`، ويؤدي إلى نتيجة يومية جديدة عبر `calculation_runs`.
8. يجب أن يكون اعتماد الدفعة وإدخال السجلات المقبولة عملية Transaction واحدة أو دفعات ذرية قابلة للاستئناف دون ازدواج.

## 16. تصميم مكان التوقيع الأساسي

1. `employee_primary_locations` هو المصدر التاريخي للموقع الأساسي.
2. يختار Service السجل الذي يغطي `attendance_date`، لا أحدث سجل في قاعدة البيانات.
3. يسجل `raw_attendance_records.matched_location_id` موقع التوقيع الوارد أو المطابق.
4. تحفظ النتيجة في `daily_attendance_results.primary_location_id`, `actual_location_id`, و`location_match_status`.
5. تغيير موقع الموظف لا يعيد كتابة النتائج السابقة. إعادة الاحتساب، إن اعتمدت بأثر رجعي، تنشئ إصدار نتيجة جديدًا.
6. اختلاف الموقع لا يعد مخالفة تلقائيًا؛ يسجل أولًا في `location_match_status` كحالة **Location Mismatch** قابلة للمراجعة والتقرير.
7. يتحول Location Mismatch إلى مخالفة فقط إذا نصت نسخة سياسة الدوام النافذة في تاريخ الحضور على ذلك، مع حفظ قاعدة التحويل في لقطة الاحتساب.

## 17. تصميم المخالفات والمعالجات والاعتمادات

1. المخالفة نتيجة مشتقة ترتبط بإصدار يومي محدد وبنسخة تفسير للقاعدة.
2. طلب المعالجة سجل مستقل لا يغير المخالفة أو السجل الخام مباشرة.
3. بعد اعتماد الطلب ينشأ أو يعتمد `administrative_adjustment`، ثم يعاد الاحتساب.
4. قالب الاعتماد قابل للإصدار، بينما `approval_step_instances` تحفظ لقطة المراحل للطلب الجاري.
5. القرارات Append-only، ولا يعدل قرار سابق. التصحيح يشير إلى القرار السابق.
6. يمنع Service أن يكون `decided_by` هو منشئ الطلب أو المستفيد منه عند انطباق قاعدة منع الاعتماد الذاتي.
7. يطبق أثر الموافقة مرة واحدة فقط باستخدام حالة واضحة وقيد فريد على `resolution_request_id` في المعالجة.
8. كل انتقال حالة أو قرار أو تطبيق معالجة يسجل في `audit_logs`.

## 18. ERD نصي

```text
users --< user_roles >-- roles --< role_permissions >-- permissions
  |
  +--< user_department_scopes >-- departments --< departments (children)

users 0..1 -------- 0..1 employees (optional OneToOne; no automatic account)

employees --1 employee_identities
employees --< employment_assignments >-- departments
employment_assignments >-- job_titles
employment_assignments >-- employees (manager)
employees --< employee_primary_locations >-- locations
employees --< employee_shift_assignments >-- shifts >-- work_policies
holiday_calendars --< holidays
holiday_calendars >-- departments / locations (optional scope)

operational_periods --< import_batches
operational_periods --< calculation_runs
operational_periods --< period_locks
operational_periods --< report_exports

import_batches --< import_rows --0..1 raw_attendance_records
import_batches --< import_errors >--0..1 import_rows
raw_attendance_records >--0..1 employees
raw_attendance_records >--0..1 locations

calculation_runs --< daily_attendance_results >-- employees
daily_attendance_results >-- departments
daily_attendance_results >-- work_policies / shifts / locations
daily_attendance_results --< daily_attendance_sources >-- raw_attendance_records
employees --< administrative_adjustments >--0..1 resolution_requests
departments --< period_locks

daily_attendance_results --< violations >-- violation_types
violations --< resolution_requests --< resolution_attachments
resolution_requests --1 approval_instances >-- approval_workflows
approval_workflows --< approval_workflow_steps >-- roles
approval_instances --< approval_step_instances >-- approval_workflow_steps
approval_step_instances --< approval_decisions >-- users

users --< notifications
users --< report_exports
users --< audit_logs
departments --< audit_logs
system_settings (independent reference settings; changes recorded in audit_logs)
```

الرموز: `--1` علاقة واحدة، `--0..1` علاقة اختيارية واحدة، `--<` واحد إلى متعدد، و`>--<` متعدد إلى متعدد عبر جدول وسيط.

## 19. الجداول المطلوبة الآن والجداول المؤجلة

### مطلوبة في V1

تغطي جداول V1 الأربعة والأربعون النطاق المستهدف بعد إضافة `operational_periods` وجداول استيراد الموظفين. لا تنشأ المجموعة كاملة دفعة واحدة؛ كل دفعة لها مراجعة وتصميم Models وMigrations واختبارات مستقلة عند طلب التنفيذ لاحقًا.

1. **الدفعة الأولى فقط:** `users`, `roles`, `permissions`, `role_permissions`, `user_roles`, `departments`, `user_department_scopes`, `audit_logs`.
2. **الدفعة الثانية:** `locations`, `job_titles`, `employees`, `employee_identities`, `employment_assignments`, `employee_primary_locations`, `employee_import_batches`, `employee_import_rows`, `employee_import_errors`.
3. **الدفعة الثالثة:** `operational_periods`, `work_policies`, `shifts`, `employee_shift_assignments`, `holiday_calendars`, `holidays`, `system_settings`.
4. **الدفعة الرابعة:** `import_batches`, `import_rows`, `import_errors`, `raw_attendance_records`.
5. **الدفعة الخامسة:** `calculation_runs`, `daily_attendance_results`, `daily_attendance_sources`, `administrative_adjustments`, `period_locks`.
6. **الدفعة السادسة:** `violation_types`, `violations`, `resolution_requests`, `resolution_attachments`، وجميع جداول الاعتماد.
7. **الدفعة السابعة:** `notifications`, `report_exports`، وتحسينات Audit Log التشغيلية.

لا يبدأ تنفيذ دفعة لاحقة تلقائيًا عند اكتمال سابقتها. يلزم اعتماد نطاق الدفعة ومراجعة العلاقات والقيود قبل إنشاء Models أو Migrations لها.

### مؤجلة لما بعد V1

- `tenants` لعزل شركات متعددة.
- `attendance_devices` و`device_sync_jobs` للتكامل المباشر مع الأجهزة.
- `leave_requests` و`leave_balances` لمنظومة الإجازات الكاملة.
- `payroll_exports` و`payroll_adjustments` للتكامل المالي.
- `notification_templates`, `notification_channels`, `delivery_attempts` للقنوات الخارجية المتقدمة.
- `report_schedules` للتقارير المجدولة.
- `api_clients`, `api_tokens`, `webhook_subscriptions` للتكاملات الخارجية.
- `sso_identities` و`mfa_devices` لتسجيل الدخول الموحد والمصادقة متعددة العوامل.
- `delegations` للتفويض الرسمي للمعتمدين؛ يستخدم إسناد يدوي مقيد في V1 إن اعتمد.
- مستودع بيانات تحليلي وجداول تجميع طويلة الأجل.
- بيانات جغرافية دقيقة وسياج جغرافي؛ الإحداثيات الحالية تمهيدية ولا تفعل مراقبة تلقائية.

## 20. ملاحظات الأداء

1. تستخدم PostgreSQL في الإنتاج لأن القيود الجزئية وExclusion Constraints وJSONB والفهرسة والتقسيم الزمني مهمة لهذا النظام.
2. أكثر الاستعلامات حساسية للفهرسة هي: الموظف والتاريخ، القسم والتاريخ، حالة المخالفة، نطاق المسؤول، وحالة الاستيراد.
3. لا يفهرس كل حقل؛ تراجع الفهارس النهائية بعد تشغيل `EXPLAIN ANALYZE` على PostgreSQL ببيانات قريبة من الحجم الحقيقي، وتحذف الفهارس المتداخلة أو غير المستخدمة.
4. يوصى بفهرسة `national_id_hash` فقط للمطابقة، وعدم فهرسة القيمة المشفرة.
5. قد يقسم `audit_logs`, `raw_attendance_records`, و`daily_attendance_results` زمنيًا عند بلوغ حجم كبير.
6. يفضل تنفيذ الاستيراد بدفعات Bulk داخل Transactions محدودة الحجم، مع منع إعادة التنفيذ بالبصمات والقيود الفريدة.
7. تحفظ لقطات النتائج اللازمة للتفسير لتجنب الاستعلامات التاريخية المعقدة، لكن يجب تحديد مخططها وإصدارها.
8. تستخدم استعلامات النطاق التنظيمي عبر Selectors موحدة؛ لا يعاد تنفيذ منطق الشجرة في كل View.
9. حقل `path_cache` اختياري للأداء، ومصدر الحقيقة يبقى `parent_id`. يجب تحديثه ذريًا عبر Service عند نقل قسم.
10. يجب اختبار الأداء على ملفات Excel بالحجم الأسبوعي المتوقع وعلى سنة كاملة من سجلات الحضور قبل الإطلاق.

### مصفوفة الفهارس المركبة الإلزامية مبدئيًا

| الأعمدة | الجداول الأساسية | الملاحظة |
|---|---|---|
| `(employee_id, attendance_date)` | raw_attendance_records, daily_attendance_results, administrative_adjustments | في النتائج اليومية يغطيه القيد Unique الذي يبدأ بالعمودين؛ لا ينشأ فهرس مطابق زائد |
| `(department_id, attendance_date)` | daily_attendance_results | فهرس جزئي للنسخة الحالية؛ تستخدم المخالفات `violation_date` لأنها التسمية الدلالية الصحيحة لذلك الجدول |
| `(national_id_hash)` | employee_identities, raw_attendance_records | القيد Unique في الهوية ينشئ فهرسه تلقائيًا، ويضاف فهرس عادي في السجل الخام |
| `(status, created_at)` | import_batches, calculation_runs, administrative_adjustments, report_exports | يطبق حيث توجد طوابير تشغيلية أو قوائم حالة زمنية، ولا يعمم على الجداول الصغيرة |
| `(employee_id, valid_from, valid_to)` | employment_assignments, employee_primary_locations, employee_shift_assignments | لاختيار العلاقة النافذة في تاريخ الحضور |
| `(department_id, valid_from, valid_to)` | employment_assignments, user_department_scopes | للتقارير التاريخية والتحقق من نطاق مسؤول القسم |

ترتيب الأعمدة جزء من القرار؛ لا يعد الفهرس المعكوس بديلًا تلقائيًا. وتراجع الفهارس المتضمنة في Unique Constraints قبل إنشاء أي Index إضافي لمنع تكلفة كتابة ومساحة بلا فائدة.

## 21. ملاحظات الأمان والخصوصية

1. تشفر قيمة السجل المدني بمفتاح خارج قاعدة البيانات، وتستخدم HMAC بمفتاح منفصل لإنشاء `national_id_hash` حتى لا يكون عرضة لقاموس مباشر.
2. لا يظهر السجل المدني الكامل في Logs أو Audit Log أو رسائل الأخطاء أو أسماء الملفات أو روابط الواجهة.
3. يقتصر فك التشفير على Service مخصص وصلاحية مستقلة، ويسجل الوصول الحساس.
4. لا تخزن كلمات المرور إلا عبر خوارزمية Django المعتمدة، ولا تخزن الأسرار في `system_settings.value_json`.
5. ملفات Excel والمرفقات تخزن خارج المسار التنفيذي، بأسماء عشوائية، وتفحص من النوع والحجم والمحتوى.
6. يطبق الوصول للقسم عند مستوى QuerySet/Selector وفي Service قبل التعديل، مع اختبارات تمنع IDOR.
7. حقول `before_json`, `after_json`, `parameters_json`, و`filters_json` تمر عبر سياسة حجب مركزية.
8. تمنح حسابات التطبيق في PostgreSQL أقل صلاحيات، ويمنع UPDATE/DELETE المباشر على جداول الأحداث الحساسة حيثما أمكن.
9. تشفر النسخ الاحتياطية، وتخضع ملفات التصدير لانتهاء صلاحية وتنزيل مصرح ومسجل.
10. لا تعتمد UUID وحدها كحماية؛ كل وصول يحتاج مصادقة وصلاحية ونطاقًا تنظيميًا.

## 22. Backup and Disaster Recovery

### مدد الاحتفاظ الأولية لبيانات النظام

| فئة البيانات | المدة الأولية | الملاحظة |
|---|---:|---|
| ملفات Excel الأصلية | 12 شهرًا | تبدأ من تاريخ الاستيراد، ثم تطهر آمنًا بعد التحقق من انتهاء الحاجة وعدم وجود تعليق نظامي |
| مرفقات المعالجات | 24 شهرًا مبدئيًا | يجوز أن تحدد سياسة نوع المعالجة مدة أطول أو أقصر |
| Audit Log | 5 سنوات | يحفظ بطريقة تمنع التعديل العادي وتدعم المراجعة |

هذه مدد تشغيلية أولية معتمدة كبداية وقابلة للتعديل بقرار رسمي من الجهة أو وفق متطلب نظامي. أي تعديل في سياسة الاحتفاظ أو عملية تطهير يسجل في `audit_logs`، ولا يعني انتهاء المدة حذفًا آليًا قبل اجتياز ضوابط التعليق القانوني والنسخ والاستعادة.

### نطاق النسخ

- قاعدة بيانات PostgreSQL كاملة، بما فيها جداول الأعمال وAudit Log.
- ملفات Excel الأصلية والمرفقات وملفات التقارير التي لم تنته مدة الاحتفاظ بها.
- تعريفات البنية وإعدادات النشر غير السرية اللازمة لإعادة بناء البيئة.
- لا تنسخ الأسرار غير اللازمة داخل ملفات التطبيق أو تصديرات قاعدة البيانات. تحفظ الأسرار الضرورية للتعافي في مدير أسرار مستقل وبسياسة نسخ خاصة به.

### سياسة النسخ اليومية والاحتفاظ

1. تنفذ نسخة كاملة يومية لقاعدة البيانات والملفات المرتبطة، مع نسخ سجل المعاملات أو نسخ تفاضلية خلال اليوم إذا دعمت منصة الإنتاج ذلك.
2. السياسة الأولية للاحتفاظ: نسخ يومية لمدة 30 يومًا، ونسخ شهرية لمدة 12 شهرًا. المدد تحتاج اعتماد مالك البيانات والمتطلبات النظامية قبل الإنتاج.
3. تحفظ النسخ خارج بيئة الإنتاج، وفي حساب أو نطاق أمني منفصل، حتى لا يؤدي حادث واحد أو اختراق حساب الإنتاج إلى فقد الأصل والنسخة معًا.
4. تشفر النسخ أثناء النقل وأثناء التخزين، وتدار مفاتيح التشفير خارج ملفات النسخ وبصلاحيات منفصلة.
5. تطبق ضوابط وصول بأقل صلاحية، مع فصل صلاحية إنشاء النسخ عن صلاحية حذفها متى سمحت المنصة.

### الاستعادة والتعافي

- **RPO الأولي المعتمد:** أربع ساعات؛ أي أن الحد الأعلى المقبول مبدئيًا لفقد البيانات هو أربع ساعات.
- **RTO الأولي المعتمد:** ثماني ساعات؛ أي أن الخدمة الأساسية تستعاد مبدئيًا خلال ثماني ساعات.
- ينفذ اختبار استعادة ربع سنوي على الأقل في بيئة معزولة، ويشمل قاعدة البيانات والملفات والتحقق من الاتساق والتشغيل الوظيفي الأساسي.
- لا تعد النسخة ناجحة لمجرد إنشائها؛ يجب التحقق آليًا من اكتمالها وحجمها وتشفيرها وقابليتها للقراءة، مع اختبار استعادة دوري فعلي.
- توثق خطة التعافي ترتيب استعادة قاعدة البيانات، والتخزين، والتطبيق، والمهام الخلفية، ثم فحوص سلامة البيانات والصلاحيات.

### المسؤوليات والتدقيق

- يكون فريق التشغيل أو البنية التحتية مسؤولًا عن تنفيذ النسخ ومراقبة نجاحها يوميًا ومعالجة التنبيهات، ويكون مالك النظام مسؤولًا عن اعتماد RPO وRTO ومدد الاحتفاظ.
- تنشأ تنبيهات عند فشل النسخة أو تأخرها أو اختلاف حجمها بصورة غير معتادة، ولا تعتمد المراقبة على فحص يدوي فقط.
- توثق كل عملية استعادة، تجريبية أو فعلية، في `audit_logs` مع الفاعل والسبب والنطاق ووقت البداية والنهاية والنتيجة، دون تسجيل أسرار أو مفاتيح. إذا كانت قاعدة البيانات غير متاحة أثناء الكارثة، يسجل الحدث أولًا في سجل حادث خارجي محمي ثم يرحل ملخصه إلى Audit Log بعد عودة الخدمة.
- تراجع صلاحيات الوصول إلى النسخ دوريًا، وتسجل عمليات التنزيل والحذف وتغيير سياسة الاحتفاظ.

## 23. المخاطر والقرارات المطلوبة قبل التنفيذ

| الرقم | القرار أو الخطر | الأثر | التوصية |
|---:|---|---|---|
| 1 | اختيار مدير الأسرار وآلية تدوير مفتاح التشفير ومفتاح HMAC المستقل | يؤثر في التشغيل والاستعادة دون تغيير التصميم المعتمد | اعتماد Runbook ومزود المفاتيح قبل أول بيانات إنتاج |
| 2 | هل رمز الموظف فريد دائمًا وهل البريد الإلكتروني فريد؟ | يؤثر في Unique Constraints | اعتماد المصدر الرسمي للهوية الوظيفية قبل دفعة الموظفين |
| 3 | قواعد السياسة التي تحول Location Mismatch إلى مخالفة | تؤثر في النتائج ولا تغير كونه حالة أولًا | اعتماد جدول القواعد مع الموارد البشرية قبل محرك المخالفات |
| 4 | حدود المناوبة العابرة لمنتصف الليل والعطل | تؤثر في إسناد اليوم رغم اعتماد الدعم في V1 | اعتماد حالات اختبار مرجعية قبل محرك الاحتساب |
| 5 | هوية المعتمد والتصعيد والتفويض | V1 مرحلة واحدة، لكن اختيار المعتمد ما زال مطلوبًا | اعتماد مصفوفة المرحلة الواحدة وتأجيل التفويض أو إضافته صراحة |
| 6 | سياسة التعديل اليدوي وفتح الفترات | خطر رقابي مرتفع | اشتراط سبب واعتماد وتدقيق وعدم تعديل الأصل |
| 7 | المصادقة النظامية على مدد الاحتفاظ الأولية | قد تفرض الجهة مددًا أطول | اعتمادها قانونيًا قبل تفعيل التطهير الآلي |
| 8 | استخدام SQLite مع قيود PostgreSQL المتقدمة | قد يخفي تداخلات زمنية في التطوير | تشغيل اختبارات تكامل على PostgreSQL قبل الدمج والإطلاق |
| 9 | أولوية التقارير والتصدير في أول إطلاق | تؤثر في `report_exports` والتخزين | تثبيت نطاق الإطلاق قبل دفعة التقارير |

## 24. معايير قبول التصميم قبل إنشاء Models

1. اعتماد قائمة الجداول وحدود V1 والجداول المؤجلة.
2. التحقق من تطبيق القرار المعتمد UUID v4 على كل جدول مملوك للمشروع، مع إبقاء استثناء جداول Django الداخلية فقط.
3. اعتماد مخطط Excel الفعلي وقواعد البصمة ومنع التكرار.
4. التحقق من تطبيق قرار تشفير السجل المدني وHMAC واعتماد إجراء إدارة المفاتيح.
5. اعتماد قواعد القسم الفعال والموقع الأساسي والمناوبة في التاريخ.
6. اعتماد أنواع المخالفات ومسارات المعالجة والاعتماد.
7. اعتماد سياسات الحذف والأرشفة والاحتفاظ والتطهير.
8. مراجعة جميع Unique Constraints وIndexes على PostgreSQL.
9. إعداد ERD رسومي لاحق مطابق لهذه الوثيقة إن طلب فريق الاعتماد ذلك.
10. عدم بدء Models أو Migrations قبل إغلاق القرارات عالية الأثر في القسم السابق.

## 25. قرارات نهائية قبل التنفيذ

### قرارات معتمدة نهائيًا

1. UUID v4 هو المفتاح الأساسي لجميع الجداول المملوكة للمشروع؛ جداول Django الداخلية الجاهزة وحدها مستثناة ولا تدخل في منطق النظام.
2. يستخدم Custom User Model من البداية، والدخول باسم المستخدم وكلمة المرور.
3. الموظف يمكن أن يوجد دون حساب، ولا ينشأ حساب لكل موظف تلقائيًا. علاقة `employees.user_id` اختيارية OneToOne، والإنشاء والربط بصلاحية إدارية فقط.
4. النظام عربي بالكامل، والمنطقة الزمنية `Asia/Riyadh`، بينما أسماء الجداول والحقول إنجليزية موحدة بصيغة `snake_case` والجداول بصيغة الجمع.
5. مسؤول القسم يرى موظفي نطاق قسمه فقط، وتفرض الصلاحية والنطاق من الخادم.
6. المطابقة تتم بالسجل المدني بعد التطبيع والحماية، ولا يستخدم السجل المدني مفتاحًا أساسيًا أو في الروابط وأسماء الملفات.
7. بيانات Excel الأصلية لا تعدل، والمعالجات الإدارية تحفظ في `administrative_adjustments` بصورة مستقلة.
8. جميع العمليات الحساسة تسجل في `audit_logs`، ومنطق الأعمال في Services وليس Views.
9. المرجعيات البسيطة `roles`, `permissions`, `job_titles`, `locations`, `violation_types` تستخدم `is_active` دون `archived_at`.
10. لا تحذف البيانات التاريخية أو التشغيلية حذفًا نهائيًا في التشغيل اليومي؛ الأرشفة والتطهير يخضعان لسياسة موثقة وتدقيق.
11. لا يكرر `working_year` في الجداول. الحقول الأساسية هي `period_start` و`period_end`، ويستخدم `operational_periods` فقط عند الحاجة إلى فترة رسمية مشتركة.
12. PostgreSQL قاعدة الإنتاج، وSQLite للتطوير المحلي فقط، مع اختبارات تكامل PostgreSQL للقيود المتقدمة.
13. التنفيذ على دفعات مستقلة، والدفعة الأولى فقط هي: `users`, `roles`, `permissions`, `role_permissions`, `user_roles`, `departments`, `user_department_scopes`, `audit_logs`.
14. تخزن القيمة الأصلية للسجل المدني مشفرة، وتستخدم HMAC مستقلة للمطابقة ومنع التكرار، ولا يظهر السجل المدني في الروابط أو Logs أو أسماء الملفات.
15. القيمة الافتراضية لـ`include_descendants` هي `False`، ولا يشمل نطاق مسؤول القسم الأقسام الفرعية دون تفعيل صريح.
16. الاسم النهائي للكيان هو OperationalPeriod، ويستخدم لتنظيم الاستيراد والقفل والاحتساب والتقارير.
17. اختلاف الموقع يسجل أولًا كحالة Location Mismatch، ولا يتحول إلى مخالفة إلا بنص صريح في السياسة.
18. يدعم V1 المناوبات العابرة لمنتصف الليل.
19. يدعم V1 مرحلة اعتماد واحدة، مع بقاء البنية قابلة للتوسعة إلى مراحل متعددة مستقبلًا.
20. مدد الاحتفاظ الأولية: Excel لمدة 12 شهرًا، والمرفقات 24 شهرًا مبدئيًا حسب السياسة، وAudit Log لمدة خمس سنوات.
21. RPO الأولي أربع ساعات وRTO الأولي ثماني ساعات.
22. يستخدم تشفير AES-256-GCM عبر `PII_ENCRYPTION_KEY`، وتستخدم HMAC-SHA256 عبر مفتاح مستقل `NATIONAL_ID_HMAC_KEY`، دون قيم افتراضية.
23. يحمل كل سجل مشفر `encryption_key_version`، ويكون إصدار التطوير الأول `v1`.
24. الرقم الوظيفي اختياري وفريد عند وجوده، ولا يولد النظام رقمًا وهميًا.
25. تعتمد جداول استيراد الموظفين المستقلة الثلاثة وقالب الأعمدة العربية المحدد في DB-003.
26. يعتمد قالب الحضور الأسبوعي الرسمي V1 كتقرير تجميعي متكرر لكل موظف وفق القسم 15، مع مطابقة HMAC فقط ومنع تكرار الملف والسجل بالبصمات المعتمدة، وتأجيل النتائج النهائية والمخالفات إلى محركاتها المخصصة.

### قرارات ما زالت تحتاج موافقة

1. مزود إدارة الأسرار الإنتاجي وإجراء تدوير واستعادة المفاتيح المعتمدة؛ الخوارزميات وأسماء المتغيرات محسومة.
2. فريدية البريد الإلكتروني.
3. تفاصيل سياسات تحويل Location Mismatch وقواعد العطل والعمل الإضافي وحواف المناوبات.
4. المعتمد في المرحلة الواحدة والتفويض والتصعيد ومدد المعالجة.
5. المصادقة النظامية النهائية على مدد الاحتفاظ الأولية وسياسة التعليق القانوني.
6. أولوية التقارير والتصدير داخل أول إطلاق تشغيلي.

## ملحق: قالب تقرير البصمة الأسبوعي V1

اعتمد النظام قالب `weekly_summary_v1` بوصفه تقريرًا تجميعيًا أسبوعيًا، وليس جدولًا مسطحًا. يتكون القالب من عنوان للفترة، وصف رؤوس، ومجموعة أيام لكل موظف، وصف «المجموع» بعد كل مجموعة، وقد يحتوي خلايا مدمجة أو صفوفًا فارغة.

### قواعد القراءة

- اكتشاف صف الرؤوس بأسماء الأعمدة، وليس بمواقعها الثابتة.
- حمل آخر سجل مدني واسم ومسمى وظيفي صالح إلى صفوف الأيام التابعة داخل مجموعة الموظف.
- تجاهل صف العنوان، وصف الرؤوس، وصفوف المجموع، والصفوف الفارغة.
- إنشاء صف استيراد مستقل لكل تاريخ دوام صالح.
- تطبيع الأرقام العربية والفارسية، والتواريخ التي يسبقها اسم اليوم، والأوقات والمدد.
- تحويل الشرطات والقيم الفارغة إلى `NULL`.
- المطابقة مع البيانات الأساسية للموظف بواسطة HMAC للسجل المدني فقط، وعدم إنشاء موظف من ملف البصمة.
- حفظ الصف الأصلي مشفرًا، وإظهار السجل المدني مقنعًا فقط.
- اعتبار اختلاف الموقع حالة تحليلية وتحذيرًا، لا مخالفة نهائية تلقائية.
- منع تكرار الملف ببصمة SHA-256، ومنع تكرار السجل اليومي ببصمة مستقرة.
- الاعتماد ذري داخل `transaction.atomic` وغير قابل للتنفيذ مرتين.

### جداول المرحلة

- `import_batches`
- `import_rows`
- `import_errors`
- `raw_attendance_records`

لا تنشئ هذه المرحلة النتائج اليومية النهائية أو المخالفات؛ تُبنى لاحقًا فوق السجلات الخام المعتمدة.

## ملحق تنفيذي: محرك احتساب الحضور اليومي V1

تم تنفيذ الطبقة الأولى من محرك الاحتساب عبر الكيانين `calculation_runs` و`daily_attendance_results`.

- يحفظ `calculation_runs` كل تشغيل أولي أو إعادة احتساب، والفترة، والحالة، وعدد النتائج.
- يحفظ `daily_attendance_results` نتيجة مستقلة وقابلة للإصدار لكل موظف ويوم.
- يحتفظ الإصدار السابق عند إعادة الاحتساب، ويكون إصدار واحد فقط هو الحالي.
- تعتمد حالة اليوم V1 على حالة المصدر ووجود بصمتي الحضور والانصراف.
- تعتمد دقائق العمل والدوام والنقص والانصراف المبكر والحضور المبكر على القيم المصدرية، مع الرجوع إلى فرق الحضور والانصراف عند غياب مدة العمل المصدرية.
- تحسب دقائق التأخر في V1 بصورة تفسيرية من: `max(نقص الدوام - الانصراف المبكر, 0)`، إلى حين تنفيذ سياسات الدوام والمناوبات الرسمية.
- تقارن بصمتا الحضور والانصراف كل واحدة بصورة مستقلة بالموقع الأساسي النافذ في تاريخ السجل.
- حالات الموقع: مطابق، حضور خارج الموقع، انصراف خارج الموقع، كلاهما خارج الموقع، غير معروف، أو لا يوجد موقع معتمد.
- اختلاف الموقع حالة تحليلية ولا يصبح مخالفة إدارية تلقائيًا.
- يتم إنشاء النتائج تلقائيًا بعد اعتماد دفعة حضور جديدة، كما يتوفر أمر إدارة لإعادة احتساب السجلات القديمة.
