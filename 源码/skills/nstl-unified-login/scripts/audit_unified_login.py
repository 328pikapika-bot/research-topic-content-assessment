# -*- coding: utf-8 -*-
"""
audit_unified_login.py — 审计NSTL统一登录接入完整性

检查项：
  - nstlAuth.ts 是否存在
  - createAuthManager 是否被初始化调用
  - X-User-Scope 是否在请求中携带
  - 是否存在本地假登录、测试账号或占位用户残留
  - requireUserScope() 是否在用户相关操作中调用
  - 后端是否有 resolve_user_scope 依赖
  - 前端是否配置了 NSTL 代理

每项输出 PASS/FAIL/WARN，最终输出审计报告摘要。
"""

import argparse
import re
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 本地假登录/测试账号/占位用户残留关键词
MOCK_PATTERNS = [
    (r"mock_user_id", "硬编码 mock_user_id"),
    (r"mock[_-]?user", "本地假用户标识"),
    (r"fake[_-]?user", "本地假用户标识"),
    (r"default\s*user", "default 用户标识"),
    (r"unknown\s*user", "unknown 用户标识"),
    (r"nstl_user_", "临时用户 nstl_user_*"),
    (r"test[_-]?account", "测试账号硬编码"),
    (r"test[_-]?user", "测试用户硬编码"),
    (r"generateNonce\s*\(", "本地生成登录 token"),
    (r"hardcode", "硬编码标记"),
]

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", ".next", ".nuxt",
    "build", ".venv", "venv", "skills", "docs",
}

RESIDUAL_ALLOWLIST_FILES = {
    "nstlAuth.ts",
    "backend_auth.py",
}

# NSTL 相关代理路径
NSTL_PROXY_PATTERNS = [
    r"/api/service/nstl",
    r"nstl-sso",
    r"isLogin",
    r"login\.nstl\.gov\.cn",
]

# ---------------------------------------------------------------------------
# 审计结果
# ---------------------------------------------------------------------------


class AuditResult:
    """单项审计结果。"""

    def __init__(self, name: str, status: str, detail: str = ""):
        self.name = name
        self.status = status  # PASS / FAIL / WARN
        self.detail = detail

    def __str__(self) -> str:
        symbol = {"PASS": "OK", "FAIL": "FAIL", "WARN": "WARN"}.get(self.status, "?")
        line = f"  [{self.status}] {symbol} {self.name}"
        if self.detail:
            line += f"\n       {self.detail}"
        return line


# ---------------------------------------------------------------------------
# 检查函数
# ---------------------------------------------------------------------------


def find_files(project_dir: Path, extensions: list[str] | None = None) -> list[Path]:
    """递归查找项目目录下的文件，排除 node_modules、.git、__pycache__ 等。"""
    results: list[Path] = []

    for path in project_dir.rglob("*"):
        if any(skip in path.parts for skip in SKIP_DIRS):
            continue
        if not path.is_file():
            continue
        if extensions:
            if path.suffix in extensions:
                results.append(path)
        else:
            results.append(path)

    return results


def read_file_safe(path: Path) -> str:
    """安全读取文件内容，忽略编码错误。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def is_skipped_path(path: Path) -> bool:
    return any(skip in path.parts for skip in SKIP_DIRS)


def is_auth_template_file(path: Path) -> bool:
    return path.name == "nstlAuth.ts"


def is_backend_auth_helper(path: Path) -> bool:
    return path.name == "backend_auth.py"


def has_usage_call(content: str, function_name: str) -> bool:
    pattern = re.compile(rf"(?<!function\s)(?<!async\sfunction\s)(?<!def\s){function_name}\s*\(")
    return bool(pattern.search(content))


def check_nstl_auth_exists(project_dir: Path) -> AuditResult:
    """检查 nstlAuth.ts 是否存在。"""
    candidates = [c for c in project_dir.rglob("nstlAuth.ts") if not is_skipped_path(c)]

    if candidates:
        paths = ", ".join(str(c.relative_to(project_dir)) for c in candidates)
        return AuditResult("nstlAuth.ts 存在", "PASS", f"找到: {paths}")
    return AuditResult("nstlAuth.ts 存在", "FAIL", "未找到 nstlAuth.ts 文件")


def check_create_auth_manager_init(project_dir: Path) -> AuditResult:
    """检查 createAuthManager 是否被初始化调用。"""
    ts_files = find_files(project_dir, extensions=[".ts", ".tsx", ".js", ".jsx", ".vue"])
    found_in: list[str] = []

    for f in ts_files:
        if is_auth_template_file(f):
            continue
        content = read_file_safe(f)
        if has_usage_call(content, "createAuthManager"):
            found_in.append(str(f.relative_to(project_dir)))

    if found_in:
        paths = ", ".join(found_in)
        return AuditResult("createAuthManager 初始化", "PASS", f"在以下文件中找到调用: {paths}")

    return AuditResult("createAuthManager 初始化", "FAIL", "未找到 createAuthManager() 调用，需在 main.ts 中初始化")


def check_x_user_scope_in_requests(project_dir: Path) -> AuditResult:
    """检查 X-User-Scope 是否在请求中携带。"""
    ts_files = find_files(project_dir, extensions=[".ts", ".tsx", ".js", ".jsx", ".vue"])
    found_in: list[str] = []

    for f in ts_files:
        content = read_file_safe(f)
        if re.search(r"X-User-Scope", content):
            found_in.append(str(f.relative_to(project_dir)))

    if found_in:
        paths = ", ".join(found_in)
        return AuditResult("X-User-Scope 请求头", "PASS", f"在以下文件中找到: {paths}")

    return AuditResult("X-User-Scope 请求头", "FAIL", "未找到 X-User-Scope 请求头携带逻辑")


def check_mock_residual(project_dir: Path) -> AuditResult:
    """检查是否存在本地假登录、测试账号或占位用户残留。"""
    all_files = find_files(project_dir, extensions=[".ts", ".tsx", ".js", ".jsx", ".vue", ".py"])
    findings: list[str] = []

    for f in all_files:
        # 跳过正式认证模板/依赖自身，它们内部保留的是阻断规则，不是假登录入口。
        if f.name in RESIDUAL_ALLOWLIST_FILES:
            continue

        content = read_file_safe(f)
        for pattern, desc in MOCK_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                rel = str(f.relative_to(project_dir))
                findings.append(f"{rel}: {desc}")

    if not findings:
        return AuditResult("本地假登录/占位用户残留", "PASS", "未发现本地假登录、测试账号或占位用户残留")

    detail = "; ".join(findings[:10])
    if len(findings) > 10:
        detail += f" ... 等共 {len(findings)} 处"
    return AuditResult("本地假登录/占位用户残留", "FAIL", f"发现 {len(findings)} 处残留: {detail}")


def check_require_user_scope(project_dir: Path) -> AuditResult:
    """检查 requireUserScope() 是否在用户相关操作中调用。"""
    ts_files = find_files(project_dir, extensions=[".ts", ".tsx", ".js", ".jsx", ".vue"])
    found_in: list[str] = []

    # 用户相关业务请求必须通过 requireUserScope 获取真实 X-User-Scope。
    patterns = [
        r"requireUserScope\s*\(",
    ]

    for f in ts_files:
        if is_auth_template_file(f):
            continue
        content = read_file_safe(f)
        for pat in patterns:
            if re.search(pat, content):
                found_in.append(str(f.relative_to(project_dir)))
                break

    if found_in:
        paths = ", ".join(found_in)
        return AuditResult("requireUserScope 调用", "PASS", f"在以下文件中找到: {paths}")

    return AuditResult(
        "requireUserScope 调用",
        "FAIL",
        "未找到 requireUserScope() 调用。用户相关请求不能只用 checkAuth()，必须先获取真实用户作用域。",
    )


def check_backend_resolve_user_scope(project_dir: Path) -> AuditResult:
    """检查后端是否有 resolve_user_scope 依赖。"""
    py_files = find_files(project_dir, extensions=[".py"])
    found_in: list[str] = []

    for f in py_files:
        if is_backend_auth_helper(f):
            continue
        content = read_file_safe(f)
        if re.search(r"resolve_user_scope", content):
            found_in.append(str(f.relative_to(project_dir)))

    if found_in:
        paths = ", ".join(found_in)
        return AuditResult("后端 resolve_user_scope 依赖", "PASS", f"在以下文件中找到: {paths}")

    return AuditResult(
        "后端 resolve_user_scope 依赖",
        "FAIL",
        "后端未找到 resolve_user_scope 依赖，需在用户相关路由中引入",
    )


def check_frontend_nstl_proxy(project_dir: Path) -> AuditResult:
    """检查前端是否配置了 NSTL 代理。"""
    # 检查 vite.config.ts/js
    config_files = list(project_dir.rglob("vite.config.*")) + list(project_dir.rglob("nginx*.conf"))
    config_files = [c for c in config_files if not is_skipped_path(c)]

    found_in: list[str] = []
    for cf in config_files:
        content = read_file_safe(cf)
        for pat in NSTL_PROXY_PATTERNS:
            if re.search(pat, content):
                found_in.append(str(cf.relative_to(project_dir)))
                break

    if found_in:
        paths = ", ".join(found_in)
        return AuditResult("前端 NSTL 代理配置", "PASS", f"在以下配置文件中找到: {paths}")

    # 没有找到代理配置，但可能使用了其他方式
    # 检查是否有 .env 文件中配置了代理
    env_files = list(project_dir.rglob(".env*"))
    for ef in env_files:
        content = read_file_safe(ef)
        if "nstl" in content.lower():
            found_in.append(str(ef.relative_to(project_dir)))

    if found_in:
        paths = ", ".join(found_in)
        return AuditResult("前端 NSTL 代理配置", "WARN", f"在环境变量文件中找到 NSTL 相关配置: {paths}")

    return AuditResult(
        "前端 NSTL 代理配置",
        "FAIL",
        "未找到 NSTL 代理配置，需在 vite.config.ts 或 nginx.conf 中配置 /api/service/nstl 代理",
    )


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------


def generate_report(results: list[AuditResult]) -> str:
    """生成审计报告摘要。"""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  NSTL 统一登录接入审计报告")
    lines.append("=" * 60)
    lines.append("")

    for r in results:
        lines.append(str(r))
        lines.append("")

    # 统计
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    warn_count = sum(1 for r in results if r.status == "WARN")
    total = len(results)

    lines.append("-" * 40)
    lines.append(f"  合计: {total} 项 | PASS: {pass_count} | FAIL: {fail_count} | WARN: {warn_count}")
    lines.append("-" * 40)

    if fail_count == 0 and warn_count == 0:
        lines.append("  结论: 审计通过，接入完整性满足要求")
    elif fail_count > 0:
        lines.append(f"  结论: 审计未通过，存在 {fail_count} 项 FAIL 需要修复")
    else:
        lines.append(f"  结论: 审计基本通过，存在 {warn_count} 项 WARN 建议优化")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="审计NSTL统一登录接入完整性",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              python audit_unified_login.py --project-dir ./my-project
              python audit_unified_login.py --project-dir ./my-project --output report.txt
        """),
    )
    parser.add_argument("--project-dir", required=True, help="项目根目录路径")
    parser.add_argument("--output", default="", help="将审计报告输出到指定文件")

    args = parser.parse_args()
    project_dir = Path(args.project_dir).resolve()

    if not project_dir.exists():
        print(f"错误: 项目目录不存在: {project_dir}")
        sys.exit(1)

    print(f"审计项目: {project_dir}")
    print("")

    # 执行所有检查
    results: list[AuditResult] = [
        check_nstl_auth_exists(project_dir),
        check_create_auth_manager_init(project_dir),
        check_x_user_scope_in_requests(project_dir),
        check_mock_residual(project_dir),
        check_require_user_scope(project_dir),
        check_backend_resolve_user_scope(project_dir),
        check_frontend_nstl_proxy(project_dir),
    ]

    # 生成报告
    report = generate_report(results)
    print(report)

    # 保存到文件
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"\n报告已保存到: {output_path}")

    if any(r.status == "FAIL" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
