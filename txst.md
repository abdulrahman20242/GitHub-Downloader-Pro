هل هذا مهم لو مهم اكتب الملف بعد التعديل
جميل — شغلك متقن جدًا. 👏
أضفت تحسينين عمليين ومباشرين إلى كودك:

1. **كشف ما إذا كان السيرفر يدعم الاستكمال (Range / Accept-Ranges)** قبل محاولة الاستئناف — هذا يمنع المحاولة لإرسال Range عندما لا يدعمها الخادم، ويفعل تحذير/إعادة تنزيل آمن إن لزم.
2. **استخدام `time.monotonic()`** لقياسات السرعة/الزمن (أفضل من `time.time()` للقياسات الزمنية لأنّه لا يتأثر بتغيير ساعة النظام).

أدناه استبدالان قصيران للدوال (انسخّهما مكان الدوال الأصلية في ملفك): `_download_zip` و `_download_attempt`، بالإضافة إلى نسخة محسّنة من `_update_download_ui` التي تستخدم `time.monotonic()`.

---

### استبدل هذه الدالة: `_download_zip(...)`

```py
def _download_zip(self, url, dest, expected):
    """
    تحميل مع دعم الاستكمال وإعادة المحاولة.
    يرجع (actual_size, sha256_hex).
    يرمي DownloadError أو CancelledError.
    """
    retry = 0
    downloaded = 0
    sha256 = hashlib.sha256()

    # ─── استكمال من ملف موجود ───
    if os.path.exists(dest):
        existing = os.path.getsize(dest)
        if existing > 0:
            downloaded = existing
            sha256 = self._hash_file(dest)

    # ─── كشف ما إذا كان السيرفر يدعم Range (Accept-Ranges) ───
    resume_supported = False
    try:
        head = self.session.head(url, allow_redirects=True, timeout=10)
        ar = head.headers.get("accept-ranges", "")
        if head.status_code in (200, 206) and "bytes" in ar.lower():
            resume_supported = True
    except requests.RequestException:
        # لا نوقف؛ سنحاول التحميل وسيُكتشف الدعم عند الرد
        resume_supported = False

    while retry <= self.MAX_RETRIES:
        try:
            return self._download_attempt(
                url, dest, expected,
                downloaded, sha256, resume_supported
            )
        except CancelledError:
            raise
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError,
            IOError
        ) as e:
            retry += 1
            if retry > self.MAX_RETRIES:
                raise DownloadError(
                    f"فشل التحميل بعد {self.MAX_RETRIES} محاولات!\n"
                    f"{type(e).__name__}: {e}"
                )

            wait = retry * self.RETRY_BASE_WAIT
            self._log(
                f"⚠️ محاولة {retry}/{self.MAX_RETRIES} بعد {wait}s ({type(e).__name__})",
                "warning"
            )

            for _ in range(wait):
                self._check_cancelled()
                time.sleep(1)

            # إعادة حساب الحالة من الملف إن بقي
            if os.path.exists(dest):
                downloaded = os.path.getsize(dest)
                sha256 = self._hash_file(dest)
            else:
                downloaded = 0
                sha256 = hashlib.sha256()

    raise DownloadError("فشل التحميل!")
```

---

### واستبدل هذه الدالة: `_download_attempt(...)`

> ملاحظة: هذه النسخة تضيف برامتر `resume_supported` وتستخدم `time.monotonic()` لقياس الزمن.

```py
def _download_attempt(
    self, url, dest, expected,
    downloaded, sha256, resume_supported
):
    """محاولة تحميل واحدة مع أو بدون استكمال"""
    headers = {}
    mode = "wb"
    start_offset = 0

    if downloaded > 0 and resume_supported:
        headers["Range"] = f"bytes={downloaded}-"
        mode = "ab"
        start_offset = downloaded
        self._log(
            f"🔄 استكمال من: {self._format_size(downloaded)}",
            "info"
        )
    elif downloaded > 0 and not resume_supported:
        # لو فيه ملف جزئي والخادم ما يدعمش الاستكمال، ننبه ونحذف حتى نعاود من الصفر
        self._log(
            "⚠️ يوجد ملف جزئي لكن الخادم لا يدعم الاستكمال. سيتم إعادة التحميل من البداية.",
            "warning"
        )
        try:
            os.remove(dest)
        except OSError:
            pass
        downloaded = 0
        sha256 = hashlib.sha256()
        mode = "wb"
        start_offset = 0

    resp = self.session.get(
        url, stream=True,
        headers=headers, timeout=30
    )

    # ─── الخادم ما يدعمش الاستكمال حالياً (رد 200 عند طلب Range) ───
    if resp.status_code == 200 and downloaded > 0:
        self._log(
            "⚠️ الخادم أعاد 200 على طلب Range — لا يدعم الاستكمال، إعادة من الصفر",
            "warning"
        )
        downloaded = 0
        start_offset = 0
        sha256 = hashlib.sha256()
        mode = "wb"
    elif resp.status_code == 206:
        # استكمال ناجح
        pass
    elif resp.status_code == 200:
        # تحميل جديد
        pass
    else:
        raise DownloadError(f"خطأ HTTP {resp.status_code}")

    start_time = time.monotonic()
    last_ui_update = start_time

    with open(dest, mode) as f:
        for chunk in resp.iter_content(
            chunk_size=self.CHUNK_SIZE
        ):
            self._check_cancelled()

            if not chunk:
                continue

            f.write(chunk)
            sha256.update(chunk)
            downloaded += len(chunk)

            now = time.monotonic()
            if now - last_ui_update >= self.UI_UPDATE_INTERVAL:
                last_ui_update = now
                self._update_download_ui(
                    downloaded, expected,
                    start_time, now,
                    start_offset
                )

    return downloaded, sha256.hexdigest()
```

---

### وأخيرًا استبدل `_update_download_ui(...)` بحيث يستخدم `time.monotonic()` (هذا مجرد تذكير — الدالة الحالية تعمل لكن من الأفضل استخدام monotonic في الأماكن التي تقيس فرق الزمن)

```py
def _update_download_ui(
    self, downloaded, expected,
    start_time, now, offset
):
    """تحديث واجهة التحميل"""
    # start_time و now مُحسبان عبر time.monotonic() في النسخة الجديدة
    elapsed = now - start_time
    new_bytes = downloaded - offset
    speed = (new_bytes / elapsed) if elapsed > 0 else 0

    if expected > 0:
        pct = (downloaded / expected) * 100
        remaining = max(0, expected - downloaded)
        eta = (remaining / speed) if speed > 0 else 0

        self._set_progress(pct)
        self._set_status(
            f"📥 {pct:.1f}%  |  {self._format_size(downloaded)} / {self._format_size(expected)}",
            "#89b4fa"
        )
        self._set_speed(
            f"⚡ {self._format_size(int(speed))}/s  |  ⏱️ {self._format_time(eta)}"
        )
    else:
        self._set_status(
            f"📥 {self._format_size(downloaded)}",
            "#89b4fa"
        )
        self._set_speed(
            f"⚡ {self._format_size(int(speed))}/s"
        )
```

---

## ملاحظات سريعة أخرى تستحق الاعتبار

* لو تحب إضافة حقل في الواجهة للسماح بإدخال `GITHUB_TOKEN` بدل الاعتماد على متغير البيئة — مفيد للمستخدمين غير المعتادين على env vars.
* تسجيل (log) إلى ملف يساعد في التحقيق عند حدوث مشاكل غامضة (`logging.FileHandler`).
* في بعض الشبكات قد تُعيد رؤوس `content-length` قيمة مختلفة بعد إعادة التوجيه، لذلك مقارنة الحجم يجب أن تأخذ بالحسبان تغييرات صغيرة — لكن أنت حالياً تقارن بدقة، وده آمن.