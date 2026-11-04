"""Isolated service-owned Writer conversion, without desktop Office automation."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import socket
import signal


def convert_docx(payload: bytes) -> bytes:
    return render_docx(payload)[1]


def render_docx(payload: bytes) -> tuple[bytes, bytes]:
    configured = os.getenv("REPORT_LIBREOFFICE_PATH", "")
    default = Path(__file__).resolve().parents[3] / ".runtime" / "libreoffice" / "program" / "soffice.com"
    executable = Path(configured) if configured else default if os.name == "nt" else Path("/usr/bin/soffice")
    if not executable.is_file():
        raise RuntimeError("PDF 转换运行时不可用。请配置服务专用 REPORT_LIBREOFFICE_PATH；Markdown 和 Word 仍可下载。")
    if os.name == "nt" and "program files" in str(executable).lower():
        raise RuntimeError("PDF 必须使用服务专用运行时，不能调用用户桌面 LibreOffice。")
    with tempfile.TemporaryDirectory(prefix="assessment-pdf-", ignore_cleanup_errors=True) as name:
        root = Path(name)
        source = root / "report.docx"
        source.write_bytes(payload)
        profile = root / "profile" / "user"
        profile.mkdir(parents=True)
        (profile / "registrymodifications.xcu").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Setup/Office"><prop oor:name="ooSetupInstCompleted" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Setup/Office"><prop oor:name="MigrationCompleted" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Jobs/Jobs/org.openoffice.Office.Jobs:Job['UpdateCheck']/Arguments"><prop oor:name="AutoCheckEnabled" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="ExperimentalMode" oor:op="fuse"><value>false</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Update/Update"><prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop></item>
</oor:items>''', encoding="utf-8")
        env = dict(os.environ)
        env["SAL_DISABLESKIA"] = "1"
        env["SAL_DISABLE_OPENCL"] = "1"
        for key in list(env):
            if key.startswith("LIBO_UPDATER_TEST_"):
                env.pop(key)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = None
        worker_process = None

        def stop_tree(child):
            if child is None or child.poll() is not None:
                return
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True,
                    timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=10)
        try:
            process = subprocess.Popen([str(executable), "-env:UserInstallation=" + (root / "profile").as_uri(),
                "--headless", "--nologo", "--nodefault", "--norestore", "--nofirststartwizard",
                f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ServiceManager"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            interpreter = executable.parent / "python.exe" if os.name == "nt" else Path("/usr/bin/python3")
            worker_process = subprocess.Popen([str(interpreter), str(Path(__file__).with_name("writer_export_worker.py")), str(port), str(root)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            worker_process.communicate(timeout=110)
            if worker_process.returncode:
                raise OSError("Writer 文档刷新或导出失败")
            process.communicate(timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            stop_tree(worker_process)
            stop_tree(process)
            raise RuntimeError("PDF 转换失败或超时，已保留 Word 和 Markdown 下载。") from exc
        target = root / "report.pdf"
        if process.returncode or not target.is_file():
            raise RuntimeError("PDF 转换未成功完成，请检查服务专用转换器与中文字体。")
        return (root / "normalized.docx").read_bytes(), target.read_bytes()
