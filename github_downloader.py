import os
import sys
import requests
import zipfile
import hashlib
import json
import threading
import time
import tempfile
import shutil
import stat
import logging
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("GitHubDownloader")


# ════════════════════════════════════════════════
# Custom Exceptions
# ════════════════════════════════════════════════

class DownloadError(Exception):
    """خطأ متوقع أثناء التحميل"""
    pass


class CancelledError(Exception):
    """المستخدم ألغى العملية"""
    pass


# ════════════════════════════════════════════════
# Main Application
# ════════════════════════════════════════════════

class GitHubDownloader:
    """
    أداة تحميل مستودعات GitHub مع:
    - استكمال التحميل بعد الانقطاع
    - إعادة المحاولة التلقائية
    - تحقق متعدد المراحل (حجم + ZIP + ملفات)
    - حماية أمنية (ZIP bomb / path traversal / symlinks)
    - واجهة رسومية كاملة
    """

    MAX_EXTRACT_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
    MAX_FILE_COUNT = 100_000
    CHUNK_SIZE = 65536
    UI_UPDATE_INTERVAL = 0.3
    MAX_RETRIES = 3
    RETRY_BASE_WAIT = 5  # ثواني

    def __init__(self, root):
        self.root = root
        self.root.title("GitHub Downloader Pro")
        self.root.geometry("720x620")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e1e2e")

        # ─── State ───
        self._cancel_event = threading.Event()
        self._download_lock = threading.Lock()
        self.is_downloading = False
        self.temp_zip_path = None
        self._worker_thread = None

        # ─── HTTP Session ───
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "GitHubDownloader/2.0",
            "Accept": "application/vnd.github.v3+json"
        })
        gh_token = os.environ.get("GITHUB_TOKEN")
        if gh_token:
            self.session.headers["Authorization"] = (
                f"token {gh_token}"
            )

        self._build_ui()

    # ════════════════════════════════════════════════
    # UI Construction
    # ════════════════════════════════════════════════

    def _build_ui(self):
        bg = "#1e1e2e"

        # ─── العنوان ───
        tk.Label(
            self.root, text="📦 GitHub Repo Downloader",
            font=("Segoe UI", 20, "bold"),
            fg="#89b4fa", bg=bg
        ).pack(pady=(15, 3))

        tk.Label(
            self.root,
            text="استكمال التحميل | تحقق تلقائي | حماية كاملة",
            font=("Segoe UI", 9), fg="#6c7086", bg=bg
        ).pack()

        # ─── الرابط ───
        url_frame = tk.Frame(self.root, bg=bg)
        url_frame.pack(pady=8, padx=30, fill="x")

        tk.Label(
            url_frame, text=":رابط المستودع",
            font=("Segoe UI", 11), fg="#cdd6f4",
            bg=bg, anchor="e"
        ).pack(anchor="e")

        self.url_entry = tk.Entry(
            url_frame, font=("Consolas", 11),
            bg="#313244", fg="#cdd6f4",
            insertbackground="#cdd6f4",
            relief="flat", justify="right"
        )
        self.url_entry.pack(fill="x", ipady=7)
        self.url_entry.insert(
            0, "https://github.com/brda38900/TAST01.git"
        )

        # ─── مجلد الحفظ ───
        save_frame = tk.Frame(self.root, bg=bg)
        save_frame.pack(pady=8, padx=30, fill="x")

        tk.Label(
            save_frame, text=":مجلد الحفظ",
            font=("Segoe UI", 11), fg="#cdd6f4",
            bg=bg, anchor="e"
        ).pack(anchor="e")

        path_row = tk.Frame(save_frame, bg=bg)
        path_row.pack(fill="x")

        tk.Button(
            path_row, text="📁", font=("Segoe UI", 10),
            bg="#45475a", fg="#cdd6f4", relief="flat",
            cursor="hand2", command=self._browse_folder
        ).pack(side="left", padx=(0, 8))

        self.path_entry = tk.Entry(
            path_row, font=("Consolas", 11),
            bg="#313244", fg="#cdd6f4",
            insertbackground="#cdd6f4",
            relief="flat", justify="right"
        )
        self.path_entry.pack(
            side="right", fill="x", expand=True, ipady=7
        )
        self.path_entry.insert(
            0, self._get_default_save_path()
        )

        # ─── شريط التقدم ───
        prog_frame = tk.Frame(self.root, bg=bg)
        prog_frame.pack(pady=5, padx=30, fill="x")

        tk.Label(
            prog_frame, text="التقدم:",
            font=("Segoe UI", 9), fg="#6c7086", bg=bg
        ).pack(anchor="w")

        self.progress = ttk.Progressbar(
            prog_frame, orient="horizontal",
            mode="determinate", length=660
        )
        self.progress.pack(fill="x")

        self.speed_label = tk.Label(
            prog_frame, text="",
            font=("Consolas", 9), fg="#6c7086", bg=bg
        )
        self.speed_label.pack(anchor="e")

        # ─── حالة ───
        self.status_label = tk.Label(
            self.root, text="جاهز ✅",
            font=("Segoe UI", 11), fg="#a6e3a1",
            bg=bg, wraplength=650, justify="right"
        )
        self.status_label.pack(pady=3)

        # ─── نتائج التحقق ───
        vf = tk.LabelFrame(
            self.root,
            text="  🔍 التحقق والتفاصيل  ",
            font=("Segoe UI", 10, "bold"),
            fg="#89b4fa", bg=bg, relief="groove"
        )
        vf.pack(pady=5, padx=30, fill="both", expand=True)

        sb = ttk.Scrollbar(vf)
        sb.pack(side="left", fill="y")

        self.verify_text = tk.Text(
            vf, height=7, font=("Consolas", 9),
            bg="#313244", fg="#cdd6f4", relief="flat",
            state="disabled", wrap="word",
            yscrollcommand=sb.set
        )
        self.verify_text.pack(
            fill="both", expand=True, padx=3, pady=3
        )
        sb.configure(command=self.verify_text.yview)

        # ─── إعداد ألوان اللوج ───
        self.verify_text.tag_config(
            "info", foreground="#cdd6f4"
        )
        self.verify_text.tag_config(
            "success", foreground="#a6e3a1"
        )
        self.verify_text.tag_config(
            "warning", foreground="#f9e2af"
        )
        self.verify_text.tag_config(
            "error", foreground="#f38ba8"
        )

        # ─── أزرار ───
        bf = tk.Frame(self.root, bg=bg)
        bf.pack(pady=10)

        self.download_btn = tk.Button(
            bf, text="⬇️ تحميل وتحقق",
            font=("Segoe UI", 13, "bold"),
            bg="#89b4fa", fg="#1e1e2e", relief="flat",
            cursor="hand2",
            command=self._start_download, width=20
        )
        self.download_btn.pack(side="right", padx=5)

        self.cancel_btn = tk.Button(
            bf, text="⛔ إلغاء",
            font=("Segoe UI", 13, "bold"),
            bg="#f38ba8", fg="#1e1e2e", relief="flat",
            cursor="hand2",
            command=self._cancel_download,
            width=12, state="disabled"
        )
        self.cancel_btn.pack(side="right", padx=5)

    # ════════════════════════════════════════════════
    # UI Helpers (thread-safe)
    # ════════════════════════════════════════════════

    def _browse_folder(self):
        """فتح نافذة اختيار مجلد"""
        folder = filedialog.askdirectory()
        if folder:
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, folder)

    def _log(self, msg, level="info"):
        """
        كتابة رسالة في اللوج مع لون حسب المستوى.
        المستويات: info, success, warning, error
        """
        logger.info(msg)

        def _update():
            self.verify_text.configure(state="normal")
            self.verify_text.insert(
                "end", msg + "\n", level
            )
            self.verify_text.see("end")
            self.verify_text.configure(state="disabled")

        self.root.after(0, _update)

    def _clear_log(self):
        """مسح اللوج"""
        def _clear():
            self.verify_text.configure(state="normal")
            self.verify_text.delete("1.0", "end")
            self.verify_text.configure(state="disabled")

        self.root.after(0, _clear)

    def _set_status(self, text, color="#cdd6f4"):
        """تحديث نص الحالة"""
        self.root.after(
            0,
            lambda: self.status_label.configure(
                text=text, fg=color
            )
        )

    def _set_speed(self, text):
        """تحديث نص السرعة"""
        self.root.after(
            0,
            lambda: self.speed_label.configure(text=text)
        )

    def _set_progress(self, val):
        """تحديث شريط التقدم (0-100)"""
        self.root.after(
            0,
            lambda: self.progress.configure(
                value=min(val, 100)
            )
        )

    # ════════════════════════════════════════════════
    # Utilities
    # ════════════════════════════════════════════════

    @staticmethod
    def _get_default_save_path():
        """تحديد مسار الحفظ الافتراضي حسب نظام التشغيل"""
        if sys.platform == "win32":
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows"
                    r"\CurrentVersion\Explorer"
                    r"\Shell Folders"
                )
                desktop = winreg.QueryValueEx(
                    key, "Desktop"
                )[0]
                winreg.CloseKey(key)
                if os.path.isdir(desktop):
                    return desktop
            except Exception:
                pass

        desktop = os.path.join(
            os.path.expanduser("~"), "Desktop"
        )
        if os.path.isdir(desktop):
            return desktop
        return os.path.expanduser("~")

    @staticmethod
    def _format_size(b):
        """تنسيق حجم الملف بوحدات مقروءة"""
        if b < 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        idx = 0
        size = float(b)
        while size >= 1024 and idx < len(units) - 1:
            size /= 1024
            idx += 1
        if idx == 0:
            return f"{int(size)} {units[idx]}"
        return f"{size:.1f} {units[idx]}"

    @staticmethod
    def _format_time(sec):
        """تنسيق الوقت بصيغة مقروءة"""
        sec = max(0, sec)
        if sec < 60:
            return f"{int(sec)}s"
        elif sec < 3600:
            m, s = divmod(int(sec), 60)
            return f"{m}m {s}s"
        h, r = divmod(int(sec), 3600)
        m, _ = divmod(r, 60)
        return f"{h}h {m}m"

    @staticmethod
    def _parse_url(url):
        """استخراج owner و repo من رابط GitHub"""
        url = url.strip().rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        for prefix in [
            "https://github.com/",
            "http://github.com/",
            "github.com/"
        ]:
            if url.startswith(prefix):
                url = url[len(prefix):]
                break
        parts = url.split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return parts[0], parts[1]
        return None, None

    @staticmethod
    def _is_safe_path(base, target):
        """التحقق من أن المسار آمن ضد path traversal"""
        try:
            abs_base = os.path.abspath(base)
            abs_target = os.path.abspath(target)
            return (
                os.path.commonpath(
                    [abs_base, abs_target]
                ) == abs_base
            )
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _get_free_space(path):
        """الحصول على المساحة المتاحة"""
        try:
            return shutil.disk_usage(path).free
        except (OSError, AttributeError):
            return float('inf')

    def _check_cancelled(self):
        """فحص إذا تم الإلغاء — يرمي CancelledError"""
        if self._cancel_event.is_set():
            raise CancelledError("تم الإلغاء")

    def _cancel_download(self):
        """إلغاء التحميل الحالي"""
        self._cancel_event.set()
        self._set_status(
            "⛔ جاري الإلغاء...", "#f38ba8"
        )

    def _cleanup_temp(self):
        """حذف الملف المؤقت بشكل آمن"""
        with self._download_lock:
            if (
                self.temp_zip_path
                and os.path.exists(self.temp_zip_path)
            ):
                try:
                    os.remove(self.temp_zip_path)
                    logger.info(
                        f"Cleaned temp:"
                        f" {self.temp_zip_path}"
                    )
                except OSError as e:
                    logger.warning(
                        f"Failed to clean temp: {e}"
                    )
                finally:
                    self.temp_zip_path = None

    def _open_folder(self, path):
        """فتح المجلد في مستكشف الملفات"""
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(
                    ["open", path], check=False
                )
            else:
                import subprocess
                subprocess.run(
                    ["xdg-open", path], check=False
                )
        except Exception as e:
            logger.warning(f"Cannot open folder: {e}")

    # ════════════════════════════════════════════════
    # GitHub API
    # ════════════════════════════════════════════════

    def _detect_branch(self, owner, repo):
        """اكتشاف الفرع الافتراضي للمستودع"""
        try:
            r = self.session.get(
                f"https://api.github.com/repos"
                f"/{owner}/{repo}",
                timeout=10
            )
            if r.status_code == 200:
                try:
                    branch = r.json().get(
                        "default_branch"
                    )
                except (
                    json.JSONDecodeError, ValueError
                ):
                    branch = None
                if branch:
                    return branch

            if r.status_code == 403:
                self._log(
                    "⚠️ GitHub API rate limit!"
                    " جرب تضيف GITHUB_TOKEN",
                    "warning"
                )
        except requests.RequestException:
            pass

        for branch in ["main", "master"]:
            try:
                r = self.session.head(
                    f"https://github.com/{owner}"
                    f"/{repo}/archive/refs"
                    f"/heads/{branch}.zip",
                    timeout=10,
                    allow_redirects=True
                )
                if r.status_code == 200:
                    return branch
            except requests.RequestException:
                continue

        return None

    def _get_api_files(self, owner, repo, branch):
        """جلب قائمة الملفات من GitHub API"""
        url = (
            f"https://api.github.com/repos"
            f"/{owner}/{repo}"
            f"/git/trees/{branch}?recursive=1"
        )
        try:
            r = self.session.get(url, timeout=15)

            if r.status_code == 403:
                self._log(
                    "⚠️ API rate limit!"
                    " جرب GITHUB_TOKEN",
                    "warning"
                )
                return None, False

            if r.status_code != 200:
                return None, False

            try:
                data = r.json()
            except (
                json.JSONDecodeError, ValueError
            ):
                self._log(
                    "⚠️ استجابة غير صالحة من API",
                    "warning"
                )
                return None, False

            truncated = data.get("truncated", False)
            files = {}
            for item in data.get("tree", []):
                if item.get("type") == "blob":
                    files[item["path"]] = {
                        "size": item.get("size", 0),
                        "sha": item.get("sha", "")
                    }
            return files, truncated

        except requests.RequestException:
            return None, False

    # ════════════════════════════════════════════════
    # Download Flow
    # ════════════════════════════════════════════════

    def _start_download(self):
        """بدء عملية التحميل في thread منفصل"""
        if self.is_downloading:
            return

        self._cancel_event.clear()
        self.is_downloading = True
        self.download_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self._set_progress(0)
        self._clear_log()
        self._set_status(
            "⏳ جاري البدء...", "#f9e2af"
        )
        self._set_speed("")

        self._worker_thread = threading.Thread(
            target=self._worker, daemon=True
        )
        self._worker_thread.start()

    def _worker(self):
        """Worker thread رئيسي"""
        try:
            self._do_download()
        except CancelledError:
            self._finish_cancelled()
        except DownloadError as e:
            self._finish_error(str(e))
        except Exception as e:
            logger.exception("Unexpected error")
            self._finish_error(
                f"خطأ غير متوقع: {e}"
            )
        finally:
            self.is_downloading = False
            self._cleanup_temp()

    def _do_download(self):
        """
        تدفق التحميل الرئيسي.
        يرمي DownloadError أو CancelledError.
        """
        url = self.url_entry.get().strip()
        save = self.path_entry.get().strip()

        # ─── تحقق من المدخلات ───
        if not url:
            raise DownloadError("أدخل الرابط!")
        if not save or not os.path.isdir(save):
            raise DownloadError(
                "مجلد الحفظ غير صحيح!"
            )

        owner, repo = self._parse_url(url)
        if not owner:
            raise DownloadError(
                "رابط غير صحيح!\n"
                "الصيغة: "
                "https://github.com/owner/repo"
            )

        # ─── اكتشاف الفرع ───
        self._set_status(
            "🔍 بحث عن المستودع...", "#89b4fa"
        )
        branch = self._detect_branch(owner, repo)
        if not branch:
            raise DownloadError(
                "مستودع غير موجود أو خاص!\n"
                f"{owner}/{repo}"
            )

        self._log(
            f"📂 {owner}/{repo} 🌿 {branch}",
            "info"
        )

        # ─── جلب معلومات API ───
        self._set_status(
            "🔍 فحص الملفات...", "#89b4fa"
        )
        api_files, truncated = self._get_api_files(
            owner, repo, branch
        )
        if api_files:
            total_size = sum(
                f["size"] for f in api_files.values()
            )
            msg = (
                f"✅ API: {len(api_files)} ملف"
                f" ({self._format_size(total_size)})"
            )
            if truncated:
                msg += " ⚠️ قائمة جزئية"
            self._log(msg, "success")
        else:
            self._log(
                "⚠️ API غير متاح، متابعة...",
                "warning"
            )

        self._check_cancelled()

        # ─── حجم ZIP ───
        zip_url = (
            f"https://github.com/{owner}/{repo}"
            f"/archive/refs/heads/{branch}.zip"
        )
        expected_size = self._get_remote_size(zip_url)

        if expected_size > 0:
            self._log(
                f"📦 ZIP:"
                f" {self._format_size(expected_size)}",
                "info"
            )
            free = self._get_free_space(save)
            needed = expected_size * 3
            if free < needed:
                raise DownloadError(
                    f"مساحة غير كافية!\n"
                    f"مطلوب:"
                    f" ~{self._format_size(needed)}\n"
                    f"متاح:"
                    f" {self._format_size(free)}"
                )

        self._check_cancelled()

        # ─── تحميل ZIP ───
        self._set_status(
            "📥 جاري التحميل...", "#89b4fa"
        )
        fd, tmp_path = tempfile.mkstemp(
            suffix=".zip", prefix=f"gh_{repo}_"
        )
        os.close(fd)

        with self._download_lock:
            self.temp_zip_path = tmp_path

        actual_size, zip_hash = self._download_zip(
            zip_url, tmp_path, expected_size
        )

        # ─── تحقق ① حجم ───
        if expected_size > 0:
            if actual_size == expected_size:
                self._log(
                    f"✅ ①: حجم مطابق"
                    f" ({self._format_size(actual_size)})",
                    "success"
                )
            else:
                raise DownloadError(
                    f"تحميل غير مكتمل!\n"
                    f"متوقع:"
                    f" {self._format_size(expected_size)}\n"
                    f"فعلي:"
                    f" {self._format_size(actual_size)}"
                )

        self._set_progress(100)

        # ─── تحقق ② سلامة ZIP ───
        self._set_status(
            "🔍 فحص سلامة ZIP...", "#f9e2af"
        )
        self._verify_zip_integrity(tmp_path)
        self._log("✅ ②: ZIP سليم", "success")

        self._check_cancelled()

        # ─── فك الضغط ───
        self._set_status(
            "📂 فك الضغط...", "#f9e2af"
        )
        self._set_speed("")
        self._set_progress(0)

        dest = self._unique_path(save, repo)
        self._extract_zip(tmp_path, dest)

        self._cleanup_temp()

        # ─── تحقق ③+④ ملفات ───
        self._verify_extracted_files(
            dest, api_files, truncated
        )

        # ─── تقرير ───
        file_count = self._count_files(dest)
        self._save_report(
            dest, owner, repo, branch,
            zip_hash, actual_size, file_count
        )

        self._finish_success(dest, file_count)

    # ════════════════════════════════════════════════
    # Remote Size
    # ════════════════════════════════════════════════

    def _get_remote_size(self, url):
        """الحصول على حجم الملف من الخادم"""
        try:
            resp = self.session.head(
                url, allow_redirects=True, timeout=15
            )
            return int(
                resp.headers.get("content-length", 0)
            )
        except (
            requests.RequestException, ValueError
        ):
            return 0

    # ════════════════════════════════════════════════
    # Download with Resume + Retry
    # ════════════════════════════════════════════════

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

        while retry <= self.MAX_RETRIES:
            try:
                return self._download_attempt(
                    url, dest, expected,
                    downloaded, sha256
                )
            except CancelledError:
                raise
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions
                .ChunkedEncodingError,
                IOError
            ) as e:
                retry += 1
                if retry > self.MAX_RETRIES:
                    raise DownloadError(
                        f"فشل التحميل بعد"
                        f" {self.MAX_RETRIES}"
                        f" محاولات!\n"
                        f"{type(e).__name__}: {e}"
                    )

                wait = retry * self.RETRY_BASE_WAIT
                self._log(
                    f"⚠️ محاولة"
                    f" {retry}/{self.MAX_RETRIES}"
                    f" بعد {wait}s"
                    f" ({type(e).__name__})",
                    "warning"
                )

                for _ in range(wait):
                    self._check_cancelled()
                    time.sleep(1)

                if os.path.exists(dest):
                    downloaded = os.path.getsize(dest)
                    sha256 = self._hash_file(dest)
                else:
                    downloaded = 0
                    sha256 = hashlib.sha256()

        raise DownloadError("فشل التحميل!")

    def _download_attempt(
        self, url, dest, expected,
        downloaded, sha256
    ):
        """محاولة تحميل واحدة مع أو بدون استكمال"""
        headers = {}
        mode = "wb"
        start_offset = 0

        if downloaded > 0:
            headers["Range"] = f"bytes={downloaded}-"
            mode = "ab"
            start_offset = downloaded
            self._log(
                f"🔄 استكمال من:"
                f" {self._format_size(downloaded)}",
                "info"
            )

        resp = self.session.get(
            url, stream=True,
            headers=headers, timeout=30
        )

        # ─── الخادم ما يدعمش الاستكمال ───
        if resp.status_code == 200 and downloaded > 0:
            self._log(
                "⚠️ الخادم لا يدعم الاستكمال،"
                " إعادة من الصفر",
                "warning"
            )
            downloaded = 0
            start_offset = 0
            sha256 = hashlib.sha256()
            mode = "wb"
        elif resp.status_code == 206:
            pass  # استكمال ناجح
        elif resp.status_code == 200:
            pass  # تحميل جديد
        else:
            raise DownloadError(
                f"خطأ HTTP {resp.status_code}"
            )

        start_time = time.time()
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

                now = time.time()
                if (
                    now - last_ui_update
                    >= self.UI_UPDATE_INTERVAL
                ):
                    last_ui_update = now
                    self._update_download_ui(
                        downloaded, expected,
                        start_time, now,
                        start_offset
                    )

        return downloaded, sha256.hexdigest()

    def _hash_file(self, path):
        """
        حساب SHA256 لملف موجود.
        يرجع كائن hashlib.sha256 قابل للتحديث
        لاستكمال الحساب عند إضافة بيانات جديدة.
        """
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256

    def _update_download_ui(
        self, downloaded, expected,
        start_time, now, offset
    ):
        """تحديث واجهة التحميل"""
        elapsed = now - start_time
        new_bytes = downloaded - offset
        speed = (
            new_bytes / elapsed if elapsed > 0 else 0
        )

        if expected > 0:
            pct = (downloaded / expected) * 100
            remaining = expected - downloaded
            eta = (
                remaining / speed if speed > 0 else 0
            )

            self._set_progress(pct)
            self._set_status(
                f"📥 {pct:.1f}%  |  "
                f"{self._format_size(downloaded)}"
                f" / "
                f"{self._format_size(expected)}",
                "#89b4fa"
            )
            self._set_speed(
                f"⚡"
                f" {self._format_size(int(speed))}/s"
                f"  |  "
                f"⏱️ {self._format_time(eta)}"
            )
        else:
            self._set_status(
                f"📥"
                f" {self._format_size(downloaded)}",
                "#89b4fa"
            )
            self._set_speed(
                f"⚡"
                f" {self._format_size(int(speed))}/s"
            )

    # ════════════════════════════════════════════════
    # ZIP Verification
    # ════════════════════════════════════════════════

    def _verify_zip_integrity(self, path):
        """
        تحقق من سلامة ملف ZIP.
        يرمي DownloadError إذا كان تالفاً.
        """
        if not zipfile.is_zipfile(path):
            raise DownloadError(
                "الملف المحمل ليس ZIP صالح!"
            )

        try:
            with zipfile.ZipFile(path, 'r') as zf:
                bad = zf.testzip()
                if bad:
                    raise DownloadError(
                        f"ZIP تالف! ملف معطوب: {bad}"
                    )

                total_uncompressed = sum(
                    info.file_size
                    for info in zf.infolist()
                )
                if (
                    total_uncompressed
                    > self.MAX_EXTRACT_SIZE
                ):
                    raise DownloadError(
                        f"ZIP كبير جداً!\n"
                        f"الحجم بعد الفك:"
                        f" {self._format_size(total_uncompressed)}\n"
                        f"الحد الأقصى:"
                        f" {self._format_size(self.MAX_EXTRACT_SIZE)}"
                    )

                file_count = len([
                    i for i in zf.infolist()
                    if not i.is_dir()
                ])
                if file_count > self.MAX_FILE_COUNT:
                    raise DownloadError(
                        f"عدد ملفات كبير جداً!"
                        f" {file_count:,} ملف\n"
                        f"الحد الأقصى:"
                        f" {self.MAX_FILE_COUNT:,}"
                    )

        except zipfile.BadZipFile:
            raise DownloadError("ZIP تالف!")

    # ════════════════════════════════════════════════
    # Extract
    # ════════════════════════════════════════════════

    @staticmethod
    def _unique_path(base, name):
        """إنشاء مسار فريد بإضافة _1, _2, ..."""
        path = os.path.join(base, name)
        if not os.path.exists(path):
            return path
        counter = 1
        while os.path.exists(f"{path}_{counter}"):
            counter += 1
        return f"{path}_{counter}"

    # ──────────────────────────────────────
    # ✅ إصلاح #1: إرجاع البادئة المشتركة
    #    الكاملة بدل الجزء الأول فقط
    # ──────────────────────────────────────
    @staticmethod
    def _detect_root_folder(names):
        """
        اكتشاف المجلد الجذري المشترك في ZIP.
        يرجع المسار الكامل المشترك مثل:
          "repo-main"         (مستوى واحد)
          "repo-main/subdir"  (مستويات متعددة)
        """
        if not names:
            return ""

        parts_list = []
        for name in names:
            name = name.replace("\\", "/").strip("/")
            if not name:
                continue
            parts_list.append(name.split("/"))

        if not parts_list:
            return ""

        common = []
        for level_parts in zip(*parts_list):
            if all(
                p == level_parts[0]
                for p in level_parts
            ):
                common.append(level_parts[0])
            else:
                break

        # ✅ إرجاع المسار الكامل المشترك
        return "/".join(common) if common else ""

    def _extract_zip(self, zip_path, dest):
        """
        فك ضغط ZIP مع حماية أمنية.
        يرمي DownloadError أو CancelledError.
        """
        try:
            os.makedirs(dest, exist_ok=True)

            with zipfile.ZipFile(zip_path, 'r') as zf:
                members = zf.infolist()
                if not members:
                    raise DownloadError("ZIP فارغ!")

                total = len(members)
                names = [m.filename for m in members]
                root_folder = self._detect_root_folder(
                    names
                )
                prefix = (
                    root_folder + "/"
                    if root_folder else ""
                )

                if root_folder:
                    self._log(
                        f"📁 مجلد جذري:"
                        f" {root_folder}/",
                        "info"
                    )

                # ✅ step محسوب خارج اللوب
                ui_step = max(1, total // 100)
                skipped = 0

                for i, member in enumerate(members):
                    self._check_cancelled()

                    # ─── المسار النسبي ───
                    filename = member.filename
                    if (
                        prefix
                        and filename.startswith(prefix)
                    ):
                        rel_path = filename[len(prefix):]
                    elif (
                        filename.rstrip("/")
                        == root_folder
                    ):
                        continue
                    else:
                        rel_path = filename

                    if not rel_path or rel_path == "/":
                        continue

                    target = os.path.join(
                        dest, rel_path
                    )

                    # ─── حماية path traversal ───
                    if not self._is_safe_path(
                        dest, target
                    ):
                        self._log(
                            f"⚠️ تخطي"
                            f" (path traversal):"
                            f" {rel_path}",
                            "warning"
                        )
                        skipped += 1
                        continue

                    # ─── حماية symlink ───
                    unix_attrs = (
                        member.external_attr >> 16
                    )
                    if (
                        unix_attrs
                        and stat.S_ISLNK(unix_attrs)
                    ):
                        self._log(
                            f"⚠️ تخطي (symlink):"
                            f" {rel_path}",
                            "warning"
                        )
                        skipped += 1
                        continue

                    # ─── فك الضغط ───
                    if member.is_dir():
                        os.makedirs(
                            target, exist_ok=True
                        )
                    else:
                        parent = os.path.dirname(
                            target
                        )
                        if parent:
                            os.makedirs(
                                parent, exist_ok=True
                            )

                        with (
                            zf.open(member) as src,
                            open(target, "wb") as dst
                        ):
                            while True:
                                chunk = src.read(
                                    self.CHUNK_SIZE
                                )
                                if not chunk:
                                    break
                                dst.write(chunk)

                    # ─── تحديث التقدم ───
                    if (
                        i % ui_step == 0
                        or i == total - 1
                    ):
                        pct = (
                            (i + 1) / total
                        ) * 100
                        self._set_progress(pct)
                        self._set_status(
                            f"📂 فك الضغط"
                            f" {pct:.0f}%"
                            f" ({i + 1}/{total})",
                            "#f9e2af"
                        )

                if skipped > 0:
                    self._log(
                        f"⚠️ تم تخطي {skipped}"
                        f" عنصر غير آمن",
                        "warning"
                    )

        except (CancelledError, DownloadError):
            shutil.rmtree(dest, ignore_errors=True)
            raise
        except Exception as e:
            shutil.rmtree(dest, ignore_errors=True)
            raise DownloadError(
                f"فشل فك الضغط: {e}"
            )

    # ════════════════════════════════════════════════
    # File Verification
    # ════════════════════════════════════════════════

    def _verify_extracted_files(
        self, path, api_files, truncated
    ):
        """تحقق من الملفات المستخرجة مقابل API"""
        self._set_status(
            "🔍 تحقق نهائي...", "#f9e2af"
        )

        # ─── جمع الملفات المحلية ───
        local_files = {}
        for dirpath, _, filenames in os.walk(path):
            for filename in filenames:
                if filename.startswith(
                    "_download_report"
                ):
                    continue
                filepath = os.path.join(
                    dirpath, filename
                )
                rel = os.path.relpath(
                    filepath, path
                ).replace("\\", "/")
                try:
                    local_files[rel] = (
                        os.path.getsize(filepath)
                    )
                except OSError:
                    local_files[rel] = -1

        local_count = len(local_files)
        total_size = sum(
            s for s in local_files.values()
            if s >= 0
        )

        if not api_files:
            self._log(
                f"ℹ️ {local_count} ملف محلي"
                f" ({self._format_size(total_size)})",
                "info"
            )
            return

        # ─── مقارنة العدد ───
        api_count = len(api_files)
        if local_count == api_count:
            self._log(
                f"✅ ③: عدد الملفات مطابق"
                f" ({local_count})",
                "success"
            )
        elif truncated:
            self._log(
                f"ℹ️ ③: {local_count} محلي,"
                f" {api_count} API"
                f" (قائمة جزئية)",
                "info"
            )
        else:
            self._log(
                f"⚠️ ③: {local_count} محلي,"
                f" {api_count} API",
                "warning"
            )

        # ─── مقارنة الأحجام ───
        missing = []
        size_mismatch = 0

        for file_path, info in api_files.items():
            if file_path not in local_files:
                missing.append(file_path)
            elif (
                local_files[file_path]
                != info["size"]
            ):
                size_mismatch += 1

        if not missing and size_mismatch == 0:
            self._log(
                "✅ ④: كل الأحجام مطابقة! 🎯",
                "success"
            )
        else:
            if missing:
                self._log(
                    f"⚠️ {len(missing)} ملف ناقص",
                    "warning"
                )
                for m in missing[:5]:
                    self._log(
                        f"   ❌ {m}", "error"
                    )
                if len(missing) > 5:
                    self._log(
                        f"   ... و{len(missing) - 5}"
                        f" ملف آخر",
                        "warning"
                    )
            if size_mismatch:
                self._log(
                    f"⚠️ {size_mismatch}"
                    f" ملف بحجم مختلف",
                    "warning"
                )

    @staticmethod
    def _count_files(path):
        """عد الملفات في مجلد"""
        count = 0
        for _, _, files in os.walk(path):
            count += len(files)
        return count

    def _save_report(
        self, path, owner, repo, branch,
        zip_hash, zip_size, file_count
    ):
        """حفظ تقرير التحميل كـ JSON"""
        report = {
            "repo": f"{owner}/{repo}",
            "branch": branch,
            "sha256": zip_hash,
            "zip_size": zip_size,
            "zip_size_human": self._format_size(
                zip_size
            ),
            "files": file_count,
            "download_time": time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "tool": "GitHubDownloader/2.0",
        }

        report_path = os.path.join(
            path, "_download_report.json"
        )
        try:
            with open(
                report_path, "w", encoding="utf-8"
            ) as f:
                json.dump(
                    report, f, indent=2,
                    ensure_ascii=False
                )
            self._log(
                "📋 تقرير التحميل محفوظ", "success"
            )
        except OSError as e:
            self._log(
                f"⚠️ فشل حفظ التقرير: {e}",
                "warning"
            )

        self._log(
            f"🔑 SHA256: {zip_hash[:32]}...",
            "info"
        )

    # ════════════════════════════════════════════════
    # Finish States
    # ════════════════════════════════════════════════

    def _finish_error(self, msg):
        """عرض رسالة خطأ وإعادة الواجهة"""
        def _update():
            self.progress.configure(value=0)
            self.status_label.configure(
                text=f"❌ {msg}", fg="#f38ba8"
            )
            self.speed_label.configure(text="")
            self.download_btn.configure(
                state="normal"
            )
            self.cancel_btn.configure(
                state="disabled"
            )
            messagebox.showerror("خطأ", msg)

        self.root.after(0, _update)

    def _finish_cancelled(self):
        """عرض رسالة إلغاء وإعادة الواجهة"""
        def _update():
            self.progress.configure(value=0)
            self.status_label.configure(
                text="⛔ تم الإلغاء", fg="#f38ba8"
            )
            self.speed_label.configure(text="")
            self.download_btn.configure(
                state="normal"
            )
            self.cancel_btn.configure(
                state="disabled"
            )

        self.root.after(0, _update)

    def _finish_success(self, path, count):
        """عرض رسالة نجاح وخيار فتح المجلد"""
        def _update():
            self.progress.configure(value=100)
            self.status_label.configure(
                text=(
                    f"✅ تم تحميل {count}"
                    f" ملف بنجاح!"
                ),
                fg="#a6e3a1"
            )
            self.speed_label.configure(text="")
            self.download_btn.configure(
                state="normal"
            )
            self.cancel_btn.configure(
                state="disabled"
            )

            if messagebox.askyesno(
                "تم بنجاح! 🎉",
                f"✅ {count} ملف تم تحميله\n"
                f"📁 {path}\n\n"
                f"هل تريد فتح المجلد؟"
            ):
                self._open_folder(path)

        self.root.after(0, _update)


# ════════════════════════════════════════════════════
# Entry Point
# ════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    app = GitHubDownloader(root)

    # ──────────────────────────────────────
    # ✅ إصلاح #2: إغلاق أنظف مع انتظار
    #    الـ thread بدل الإغلاق الفوري
    # ──────────────────────────────────────
    def on_close():
        """
        إغلاق آمن:
        - لو مفيش تحميل → إغلاق فوري
        - لو فيه تحميل → إلغاء + انتظار
          الـ thread يخلص + تنظيف → إغلاق
        """
        if (
            app.is_downloading
            and app._worker_thread is not None
            and app._worker_thread.is_alive()
        ):
            if messagebox.askyesno(
                "تأكيد الإغلاق",
                "التحميل شغال، هل تريد الإغلاق؟"
            ):
                # إرسال إشارة الإلغاء
                app._cancel_event.set()

                # انتظار الـ thread بـ polling
                def wait_for_thread():
                    if app._worker_thread.is_alive():
                        # لسه شغال → انتظر 200ms
                        root.after(
                            200, wait_for_thread
                        )
                    else:
                        # خلص → نظف وأغلق
                        app._cleanup_temp()
                        root.destroy()

                root.after(200, wait_for_thread)
        else:
            app._cleanup_temp()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()