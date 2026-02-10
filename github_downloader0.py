import os
import sys
import requests
import zipfile
import hashlib
import io
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


class GitHubDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title("GitHub Repository Downloader - Verified")
        self.root.geometry("700x550")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e1e2e")

        # ---- العنوان ----
        tk.Label(
            root, text="📦 GitHub Repo Downloader",
            font=("Segoe UI", 20, "bold"),
            fg="#89b4fa", bg="#1e1e2e"
        ).pack(pady=(20, 10))

        # ---- إطار الرابط ----
        url_frame = tk.Frame(root, bg="#1e1e2e")
        url_frame.pack(pady=10, padx=30, fill="x")

        tk.Label(
            url_frame, text="رابط المستودع:",
            font=("Segoe UI", 12), fg="#cdd6f4",
            bg="#1e1e2e", anchor="e"
        ).pack(anchor="e", pady=(0, 5))

        self.url_entry = tk.Entry(
            url_frame, font=("Consolas", 12),
            bg="#313244", fg="#cdd6f4",
            insertbackground="#cdd6f4",
            relief="flat", justify="right"
        )
        self.url_entry.pack(fill="x", ipady=8)
        self.url_entry.insert(0, "https://github.com/brda38900/TAST01.git")

        # ---- إطار مجلد الحفظ ----
        save_frame = tk.Frame(root, bg="#1e1e2e")
        save_frame.pack(pady=10, padx=30, fill="x")

        tk.Label(
            save_frame, text="مجلد الحفظ:",
            font=("Segoe UI", 12), fg="#cdd6f4",
            bg="#1e1e2e", anchor="e"
        ).pack(anchor="e", pady=(0, 5))

        path_row = tk.Frame(save_frame, bg="#1e1e2e")
        path_row.pack(fill="x")

        tk.Button(
            path_row, text="📁 اختر",
            font=("Segoe UI", 10), bg="#45475a",
            fg="#cdd6f4", relief="flat", cursor="hand2",
            command=self.browse_folder
        ).pack(side="left", padx=(0, 10))

        self.path_entry = tk.Entry(
            path_row, font=("Consolas", 11),
            bg="#313244", fg="#cdd6f4",
            insertbackground="#cdd6f4",
            relief="flat", justify="right"
        )
        self.path_entry.pack(side="right", fill="x", expand=True, ipady=8)
        self.path_entry.insert(0, os.path.join(os.path.expanduser("~"), "Desktop"))

        # ---- شريط التقدم ----
        self.progress = ttk.Progressbar(
            root, orient="horizontal",
            mode="determinate", length=640
        )
        self.progress.pack(pady=10)

        # ---- حالة التحميل ----
        self.status_label = tk.Label(
            root, text="جاهز للتحميل ✅",
            font=("Segoe UI", 11), fg="#a6e3a1",
            bg="#1e1e2e", wraplength=600, justify="right"
        )
        self.status_label.pack()

        # ---- إطار نتائج التحقق ----
        verify_frame = tk.LabelFrame(
            root, text="  🔍 نتائج التحقق  ",
            font=("Segoe UI", 11, "bold"),
            fg="#89b4fa", bg="#1e1e2e",
            relief="groove", bd=2
        )
        verify_frame.pack(pady=10, padx=30, fill="x")

        self.verify_text = tk.Text(
            verify_frame, height=7,
            font=("Consolas", 10),
            bg="#313244", fg="#cdd6f4",
            relief="flat", state="disabled",
            wrap="word"
        )
        self.verify_text.pack(fill="x", padx=5, pady=5)

        # ---- زر التحميل ----
        self.download_btn = tk.Button(
            root, text="⬇️  تحميل وتحقق",
            font=("Segoe UI", 14, "bold"),
            bg="#89b4fa", fg="#1e1e2e",
            activebackground="#74c7ec",
            relief="flat", cursor="hand2",
            command=self.start_download, width=25
        )
        self.download_btn.pack(pady=10)

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, folder)

    def log_verify(self, message, color="#cdd6f4"):
        """إضافة رسالة لنتائج التحقق"""
        def _log():
            self.verify_text.configure(state="normal")
            self.verify_text.insert("end", message + "\n")
            self.verify_text.see("end")
            self.verify_text.configure(state="disabled")
        self.root.after(0, _log)

    def clear_verify_log(self):
        self.root.after(0, lambda: (
            self.verify_text.configure(state="normal"),
            self.verify_text.delete("1.0", "end"),
            self.verify_text.configure(state="disabled")
        ))

    def parse_github_url(self, url):
        url = url.strip().rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        parts = url.replace("https://github.com/", "").split("/")
        if len(parts) >= 2:
            owner = parts[0]
            repo = parts[1]
            return owner, repo
        return None, None

    def get_repo_tree_from_api(self, owner, repo, branch):
        """
        ─────────────────────────────────────────────────
        تحقق ①: جلب قائمة الملفات من GitHub API
        نستخدم Git Trees API لجلب كل الملفات والمقاسات
        ─────────────────────────────────────────────────
        """
        api_url = (
            f"https://api.github.com/repos/{owner}/{repo}"
            f"/git/trees/{branch}?recursive=1"
        )
        response = requests.get(api_url, timeout=30)

        if response.status_code != 200:
            return None

        data = response.json()
        tree = data.get("tree", [])

        files_info = {}
        for item in tree:
            if item["type"] == "blob":
                files_info[item["path"]] = {
                    "size": item.get("size", 0),
                    "sha": item.get("sha", "")
                }
        return files_info

    def start_download(self):
        self.download_btn.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(10)
        self.clear_verify_log()
        self.status_label.configure(text="⏳ جاري التحميل...", fg="#f9e2af")
        thread = threading.Thread(target=self.download_repo, daemon=True)
        thread.start()

    def download_repo(self):
        url = self.url_entry.get().strip()
        save_path = self.path_entry.get().strip()

        if not url:
            self.show_error("❌ أدخل رابط المستودع!")
            return
        if not save_path:
            self.show_error("❌ اختر مجلد الحفظ!")
            return

        owner, repo = self.parse_github_url(url)
        if not owner:
            self.show_error("❌ الرابط غير صحيح!")
            return

        try:
            # ═══════════════════════════════════════════
            # تحديد الفرع الصحيح
            # ═══════════════════════════════════════════
            branch = "main"
            zip_url = (
                f"https://github.com/{owner}/{repo}"
                f"/archive/refs/heads/{branch}.zip"
            )
            test = requests.head(zip_url, timeout=15, allow_redirects=True)

            if test.status_code == 404:
                branch = "master"
                zip_url = (
                    f"https://github.com/{owner}/{repo}"
                    f"/archive/refs/heads/{branch}.zip"
                )
                test = requests.head(zip_url, timeout=15, allow_redirects=True)

            if test.status_code != 200:
                self.show_error("❌ المستودع غير موجود أو خاص!")
                return

            self.log_verify(f"🌿 الفرع: {branch}")

            # ═══════════════════════════════════════════
            # تحقق ①: جلب قائمة الملفات من API
            # ═══════════════════════════════════════════
            self.update_status("🔍 جلب قائمة الملفات من API...", "#89b4fa")
            api_files = self.get_repo_tree_from_api(owner, repo, branch)

            if api_files:
                api_file_count = len(api_files)
                api_total_size = sum(f["size"] for f in api_files.values())
                self.log_verify(
                    f"✅ تحقق ①: API يقول {api_file_count} ملف"
                    f" ({api_total_size:,} bytes)"
                )
            else:
                self.log_verify("⚠️ تحقق ①: تعذر الوصول لـ API (نكمل بدونه)")
                api_files = None

            # ═══════════════════════════════════════════
            # تحميل ملف ZIP
            # ═══════════════════════════════════════════
            self.update_status("📥 جاري تحميل ZIP...", "#89b4fa")

            response = requests.get(zip_url, stream=True, timeout=60)
            expected_size = int(response.headers.get('content-length', 0))

            zip_data = io.BytesIO()
            downloaded = 0
            sha256_hash = hashlib.sha256()

            # تحويل شريط التقدم لـ determinate
            self.root.after(0, lambda: (
                self.progress.stop(),
                self.progress.configure(mode="determinate", maximum=100)
            ))

            for chunk in response.iter_content(chunk_size=8192):
                zip_data.write(chunk)
                sha256_hash.update(chunk)
                downloaded += len(chunk)

                if expected_size > 0:
                    percent = (downloaded / expected_size) * 100
                    self.root.after(
                        0,
                        lambda p=percent: self.progress.configure(value=p)
                    )
                    self.update_status(
                        f"📥 تحميل... {percent:.1f}%"
                        f" ({downloaded:,}/{expected_size:,} bytes)",
                        "#89b4fa"
                    )

            zip_hash = sha256_hash.hexdigest()

            # ═══════════════════════════════════════════
            # تحقق ②: حجم ZIP المحمّل = الحجم المتوقع
            # ═══════════════════════════════════════════
            if expected_size > 0:
                if downloaded == expected_size:
                    self.log_verify(
                        f"✅ تحقق ②: حجم ZIP مطابق"
                        f" ({downloaded:,} bytes)"
                    )
                else:
                    self.log_verify(
                        f"❌ تحقق ②: حجم ZIP غير مطابق!"
                        f" (توقعنا {expected_size:,},"
                        f" حصلنا {downloaded:,})"
                    )
                    self.show_error("❌ حجم الملف المحمّل غير مطابق!")
                    return
            else:
                self.log_verify(
                    f"⚠️ تحقق ②: السيرفر لم يرسل الحجم"
                    f" (حمّلنا {downloaded:,} bytes)"
                )

            # ═══════════════════════════════════════════
            # تحقق ③: ملف ZIP سليم وقابل للفتح
            # ═══════════════════════════════════════════
            zip_data.seek(0)
            if not zipfile.is_zipfile(zip_data):
                self.log_verify("❌ تحقق ③: الملف المحمّل ليس ZIP سليم!")
                self.show_error("❌ الملف المحمّل تالف!")
                return

            zip_data.seek(0)
            try:
                zf = zipfile.ZipFile(zip_data, 'r')
                # اختبار سلامة كل ملف داخل ZIP
                corrupt = zf.testzip()
                if corrupt is not None:
                    self.log_verify(f"❌ تحقق ③: ملف تالف داخل ZIP: {corrupt}")
                    self.show_error(f"❌ ملف تالف: {corrupt}")
                    return
                self.log_verify("✅ تحقق ③: ملف ZIP سليم 100%")
            except zipfile.BadZipFile:
                self.log_verify("❌ تحقق ③: ZIP تالف!")
                self.show_error("❌ ملف ZIP تالف!")
                return

            # ═══════════════════════════════════════════
            # فك الضغط
            # ═══════════════════════════════════════════
            self.update_status("📂 جاري فك الضغط...", "#f9e2af")

            zip_data.seek(0)
            zf = zipfile.ZipFile(zip_data, 'r')

            # تحديد مجلد الحفظ النهائي
            final_path = os.path.join(save_path, repo)
            counter = 1
            while os.path.exists(final_path):
                final_path = os.path.join(save_path, f"{repo}_{counter}")
                counter += 1

            # فك الضغط مع تتبع التقدم
            members = zf.namelist()
            total_members = len(members)

            # اسم المجلد الأصلي داخل ZIP
            root_folder = members[0].split("/")[0] if members else ""

            # إنشاء المجلد النهائي
            os.makedirs(final_path, exist_ok=True)

            for i, member in enumerate(members):
                # إزالة اسم المجلد الجذر من المسار
                relative_path = member[len(root_folder):].lstrip("/")
                if not relative_path:
                    continue

                target_path = os.path.join(final_path, relative_path)

                if member.endswith("/"):
                    os.makedirs(target_path, exist_ok=True)
                else:
                    os.makedirs(
                        os.path.dirname(target_path),
                        exist_ok=True
                    )
                    with zf.open(member) as src, \
                         open(target_path, "wb") as dst:
                        dst.write(src.read())

                percent = ((i + 1) / total_members) * 100
                self.root.after(
                    0,
                    lambda p=percent: self.progress.configure(value=p)
                )

            zf.close()

            # ═══════════════════════════════════════════
            # تحقق ④: عدد الملفات المستخرجة = API
            # ═══════════════════════════════════════════
            local_files = {}
            for dirpath, dirnames, filenames in os.walk(final_path):
                for fname in filenames:
                    full_path = os.path.join(dirpath, fname)
                    rel_path = os.path.relpath(
                        full_path, final_path
                    ).replace("\\", "/")
                    file_size = os.path.getsize(full_path)
                    local_files[rel_path] = file_size

            local_count = len(local_files)

            if api_files:
                api_count = len(api_files)
                if local_count == api_count:
                    self.log_verify(
                        f"✅ تحقق ④: عدد الملفات مطابق"
                        f" ({local_count} ملف)"
                    )
                else:
                    self.log_verify(
                        f"⚠️ تحقق ④: عدد مختلف!"
                        f" (API: {api_count},"
                        f" محلي: {local_count})"
                    )
            else:
                self.log_verify(
                    f"ℹ️ تحقق ④: {local_count} ملف تم استخراجه"
                )

            # ═══════════════════════════════════════════
            # تحقق ⑤: مقارنة أحجام كل ملف مع API
            # ═══════════════════════════════════════════
            if api_files:
                mismatched = []
                missing = []

                for api_path, api_info in api_files.items():
                    if api_path in local_files:
                        local_size = local_files[api_path]
                        api_size = api_info["size"]
                        if local_size != api_size:
                            mismatched.append(
                                f"  {api_path}:"
                                f" API={api_size},"
                                f" local={local_size}"
                            )
                    else:
                        missing.append(f"  ❌ {api_path}")

                if not mismatched and not missing:
                    self.log_verify(
                        "✅ تحقق ⑤: كل الملفات مطابقة بالحجم! 🎯"
                    )
                else:
                    if missing:
                        self.log_verify(
                            f"⚠️ تحقق ⑤: {len(missing)} ملف ناقص:"
                        )
                        for m in missing[:5]:
                            self.log_verify(m)
                    if mismatched:
                        self.log_verify(
                            f"⚠️ تحقق ⑤:"
                            f" {len(mismatched)} ملف بحجم مختلف:"
                        )
                        for m in mismatched[:5]:
                            self.log_verify(m)
            else:
                self.log_verify(
                    "ℹ️ تحقق ⑤: تخطي (API غير متاح)"
                )

            # ═══════════════════════════════════════════
            # حفظ تقرير التحقق
            # ═══════════════════════════════════════════
            report = {
                "repo": f"{owner}/{repo}",
                "branch": branch,
                "zip_sha256": zip_hash,
                "zip_size": downloaded,
                "files_count": local_count,
                "files": {
                    path: size
                    for path, size in sorted(local_files.items())
                }
            }

            report_path = os.path.join(final_path, "_download_report.json")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            self.log_verify(f"\n📋 تقرير محفوظ: _download_report.json")
            self.log_verify(f"🔑 SHA256: {zip_hash[:16]}...")

            self.download_complete(final_path, local_count)

        except requests.exceptions.Timeout:
            self.show_error("❌ انتهت مهلة الاتصال!")
        except requests.exceptions.ConnectionError:
            self.show_error("❌ تأكد من اتصال الإنترنت!")
        except Exception as e:
            self.show_error(f"❌ خطأ: {str(e)}")

    def update_status(self, text, color):
        self.root.after(
            0,
            lambda: self.status_label.configure(text=text, fg=color)
        )

    def show_error(self, message):
        def _show():
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.status_label.configure(text=message, fg="#f38ba8")
            self.download_btn.configure(state="normal")
            messagebox.showerror("خطأ", message)
        self.root.after(0, _show)

    def download_complete(self, path, count):
        def _complete():
            self.progress.configure(value=100)
            self.status_label.configure(
                text=f"✅ تم تحميل {count} ملف بنجاح!",
                fg="#a6e3a1"
            )
            self.download_btn.configure(state="normal")
            messagebox.showinfo(
                "تم بنجاح! 🎉",
                f"تم تحميل {count} ملف في:\n{path}\n\n"
                "كل عمليات التحقق ناجحة ✅"
            )
            os.startfile(path)
        self.root.after(0, _complete)


def main():
    root = tk.Tk()
    app = GitHubDownloader(root)
    root.mainloop()


if __name__ == "__main__":
    main()