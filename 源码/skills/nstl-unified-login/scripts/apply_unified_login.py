# -*- coding: utf-8 -*-
"""
apply_unified_login.py — 自动生成NSTL统一登录接入文件

将 assets/templates/ 下的模板文件复制到目标项目对应位置，
自动替换模板中的占位符（backend-url、local-host等），
并生成报告说明还需要手动完成的步骤。
"""

import argparse
import re
import shutil
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SKILL_DIR / "assets" / "templates"

# 模板 → 目标相对路径映射（前端）
FRONTEND_TEMPLATES = {
    "nstlAuth.ts": "src/services/nstlAuth.ts",
    "api-client.ts.example": "src/api/api-client.ts",
}

# 模板 → 目标相对路径映射（后端）
BACKEND_TEMPLATES = {
    "backend_auth.py": "app/deps/backend_auth.py",
}

# 参考模板（仅复制到 docs/ 目录供开发者参考，不替换占位符）
REFERENCE_TEMPLATES = {
    "main.ts.example": "docs/nstl-login/main.ts.example",
    "vite.config.ts.example": "docs/nstl-login/vite.config.ts.example",
    "nginx.conf.snippet": "docs/nstl-login/nginx.conf.snippet",
}

# 占位符 → 替换规则
PLACEHOLDER_REPLACEMENTS = {
    "<backend-url>": "backend_url",
    "<backend-port>": "backend_port",
    "<backend-host>": "backend_host",
    "<agent-local-host>": "local_host",
    "<agent-route-prefix>": "agent_route_prefix",
    "<frontend-port>": "frontend_port",
    "<cert-passphrase>": "cert_passphrase",
}

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def replace_placeholders(content: str, values: dict[str, str]) -> str:
    """将模板内容中的占位符替换为实际值。"""
    for placeholder, key in PLACEHOLDER_REPLACEMENTS.items():
        value = values.get(key, "")
        if value:
            content = content.replace(placeholder, value)
    return content


def copy_and_replace(
    template_name: str,
    dest_dir: Path,
    relative_path: str,
    values: dict[str, str],
    overwrite: bool = False,
) -> dict:
    """复制模板文件到目标位置并替换占位符。

    Returns:
        dict: {"path": str, "status": "created"|"skipped"|"updated"}
    """
    src = TEMPLATES_DIR / template_name
    dest = dest_dir / relative_path

    if not src.exists():
        return {"path": str(dest), "status": "error", "message": f"模板 {template_name} 不存在"}

    if dest.exists() and not overwrite:
        return {"path": str(dest), "status": "skipped", "message": "文件已存在，使用 --overwrite 覆盖"}

    content = src.read_text(encoding="utf-8")
    content = replace_placeholders(content, values)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")

    return {"path": str(dest), "status": "created"}


def build_replacement_values(args: argparse.Namespace) -> dict[str, str]:
    """从命令行参数构建占位符替换值字典。"""
    # 从 backend-url 解析 host/port
    backend_url = args.backend_url
    backend_host = "127.0.0.1"
    backend_port = "8000"

    url_match = re.match(r"https?://([^:]+):(\d+)", backend_url)
    if url_match:
        backend_host = url_match.group(1)
        backend_port = url_match.group(2)
    elif backend_url.startswith("/"):
        # 相对路径，使用默认值
        pass

    return {
        "backend_url": backend_url,
        "backend_host": backend_host,
        "backend_port": backend_port,
        "local_host": args.local_host,
        "agent_route_prefix": getattr(args, "agent_route_prefix", ""),
        "frontend_port": getattr(args, "frontend_port", "5173"),
        "cert_passphrase": getattr(args, "cert_passphrase", ""),
    }


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------


def apply_frontend(frontend_dir: Path, values: dict[str, str], overwrite: bool) -> list[dict]:
    """将前端模板复制到目标前端项目。"""
    results = []
    for template_name, relative_path in FRONTEND_TEMPLATES.items():
        result = copy_and_replace(template_name, frontend_dir, relative_path, values, overwrite)
        results.append(result)

    # 复制参考模板
    for template_name, relative_path in REFERENCE_TEMPLATES.items():
        src = TEMPLATES_DIR / template_name
        dest = frontend_dir / relative_path
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or overwrite:
                shutil.copy2(src, dest)
                results.append({"path": str(dest), "status": "created (reference)"})
            else:
                results.append({"path": str(dest), "status": "skipped (reference)"})

    return results


def apply_backend(backend_dir: Path, values: dict[str, str], overwrite: bool) -> list[dict]:
    """将后端模板复制到目标后端项目。"""
    results = []
    for template_name, relative_path in BACKEND_TEMPLATES.items():
        result = copy_and_replace(template_name, backend_dir, relative_path, values, overwrite)
        results.append(result)
    return results


def generate_report(
    frontend_results: list[dict],
    backend_results: list[dict],
    values: dict[str, str],
) -> str:
    """生成接入报告，包含已完成的操作和需要手动完成的步骤。"""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  NSTL 统一登录接入报告")
    lines.append("=" * 60)

    # 已完成的文件操作
    lines.append("")
    lines.append("## 已完成的文件操作")
    lines.append("-" * 40)

    all_results = frontend_results + backend_results
    for r in all_results:
        status = r.get("status", "unknown")
        path = r.get("path", "")
        msg = r.get("message", "")
        if status == "created" or "reference" in status:
            lines.append(f"  [OK] {status}: {path}")
        elif status == "skipped" or "skipped" in status:
            lines.append(f"  - {status}: {path} ({msg})")
        else:
            lines.append(f"  [ERR] {status}: {path} ({msg})")

    # 需要手动完成的步骤
    lines.append("")
    lines.append("## 需要手动完成的步骤")
    lines.append("-" * 40)

    manual_steps = [
        "1. 在 main.ts 中初始化 createAuthManager()，参考 docs/nstl-login/main.ts.example",
        "2. 在路由守卫或组件中调用 getAuthManager().checkAuth() 检查登录状态",
        "3. 在用户相关操作前调用 getAuthManager().requireUserScope() 获取真实用户身份",
        "4. 确保所有业务请求携带 X-User-Scope 头（参考 src/api/api-client.ts）",
        "5. 在后端路由中引入 resolve_user_scope 依赖（参考 app/deps/backend_auth.py）",
        "6. 配置 Vite 代理或 Nginx 代理（参考 docs/nstl-login/ 下的配置示例）",
        f"7. 确保本地 HTTPS 证书配置正确（域名: {values.get('local_host', '')}）",
        "8. 运行 audit_unified_login.py 审计接入完整性",
        "9. 运行 smoke_islogin.py 验证 isLogin 接口可达",
    ]

    for step in manual_steps:
        lines.append(f"  {step}")

    # 替换值摘要
    lines.append("")
    lines.append("## 占位符替换摘要")
    lines.append("-" * 40)
    for key, value in values.items():
        if value:
            lines.append(f"  {key}: {value}")
        else:
            lines.append(f"  {key}: <未指定，需手动替换>")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="自动生成NSTL统一登录接入文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              python apply_unified_login.py --frontend-dir ./my-frontend --backend-dir ./my-backend
              python apply_unified_login.py --frontend-dir ./fe --backend-dir ./be --local-host myapp.nstl.gov.cn --overwrite
        """),
    )
    parser.add_argument("--frontend-dir", required=True, help="前端项目目录路径")
    parser.add_argument("--backend-dir", required=True, help="后端项目目录路径")
    parser.add_argument("--framework", default="vue3", choices=["vue3", "react"], help="前端框架 (默认: vue3)")
    parser.add_argument("--backend-url", default="/api", help="后端地址 (默认: /api)")
    parser.add_argument("--local-host", default="<agent-local-host>.nstl.gov.cn", help="本地 NSTL 子域名")
    parser.add_argument("--agent-route-prefix", default="", help="智能体路由前缀 (用于nginx配置)")
    parser.add_argument("--frontend-port", default="5173", help="前端开发端口 (默认: 5173)")
    parser.add_argument("--cert-passphrase", default="", help="本地HTTPS证书密码")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的文件")

    args = parser.parse_args()

    frontend_dir = Path(args.frontend_dir).resolve()
    backend_dir = Path(args.backend_dir).resolve()

    # 校验目录存在性
    if not frontend_dir.exists():
        print(f"错误: 前端目录不存在: {frontend_dir}")
        sys.exit(1)
    if not backend_dir.exists():
        print(f"错误: 后端目录不存在: {backend_dir}")
        sys.exit(1)

    if not TEMPLATES_DIR.exists():
        print(f"错误: 模板目录不存在: {TEMPLATES_DIR}")
        sys.exit(1)

    # 构建替换值
    values = build_replacement_values(args)

    print(f"前端目录: {frontend_dir}")
    print(f"后端目录: {backend_dir}")
    print(f"框架: {args.framework}")
    print(f"后端地址: {args.backend_url}")
    print(f"本地域名: {args.local_host}")
    print("")

    # 执行复制和替换
    frontend_results = apply_frontend(frontend_dir, values, args.overwrite)
    backend_results = apply_backend(backend_dir, values, args.overwrite)

    # 生成报告
    report = generate_report(frontend_results, backend_results, values)
    print(report)

    # 保存报告到目标前端项目
    report_path = frontend_dir / "docs" / "nstl-login" / "apply-report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\n报告已保存到: {report_path}")


if __name__ == "__main__":
    main()
