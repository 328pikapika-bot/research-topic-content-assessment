# -*- coding: utf-8 -*-
"""审计文献检索模块接入完整性。

检查目标项目中文献检索模块的接入状态，输出结构化审计报告。

功能：
  - 检查 app/common/retrieval/ 目录是否存在
  - 检查 __init__.py 导出是否完整（LiteratureSearchClient, SearchRequest 等）
  - 检查业务代码是否正确导入（从 app.common.retrieval 导入）
  - 检查是否存在直接 Milvus/Solr/Reranker 调用
  - 检查 Adapter 文件是否存在
  - 检查文献检索网关环境变量是否配置
  - 检查 search_trace 是否可保存/返回
  - 输出审计摘要

用法：
  python scripts/audit_retrieval.py --project-dir /path/to/project
  python scripts/audit_retrieval.py --project-dir /path/to/project --output report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

EXPECTED_EXPORTS = [
    "LiteratureSearchClient",
    "SearchRequest",
    "SearchResponse",
    "SearchTrace",
    "StandardPaper",
]

# 不应直接使用的底层调用关键词
FORBIDDEN_DIRECT_CALLS = [
    r"from\s+pymilvus",
    r"import\s+pymilvus",
    r"from\s+pysolr",
    r"import\s+pysolr",
    r"MILVUS_HOST",
    r"MILVUS_PORT",
    r"solr_url",
    r"RerankerClient",
    r"reranker_client",
    r"BGEReranker",
]

ENV_URL_NAMES = [
    "LITERATURE_SEARCH_API_URL",
    "NSTL_RETRIEVAL_API_URL",
    "GOOD_QUESTION_MILVUS_API_URL",
    "MILVUS_API_URL",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="审计文献检索模块接入完整性",
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        type=Path,
        help="项目根目录",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出报告文件路径（JSON格式）",
    )
    return parser.parse_args()


class AuditResult:
    """单条审计结果。"""

    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.name = name
        self.status = status  # PASS / FAIL / WARN
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


def _check_retrieval_dir(project_dir: Path) -> AuditResult:
    """检查 app/common/retrieval/ 目录是否存在。"""
    retrieval_dir = project_dir / "backend" / "app" / "common" / "retrieval"
    if not retrieval_dir.exists():
        # 尝试直接在项目目录下查找
        retrieval_dir = project_dir / "app" / "common" / "retrieval"
    if retrieval_dir.exists():
        return AuditResult(
            "retrieval目录存在",
            "PASS",
            f"找到: {retrieval_dir}",
        )
    return AuditResult(
        "retrieval目录存在",
        "FAIL",
        "未找到 app/common/retrieval/ 目录",
    )


def _find_backend_dir(project_dir: Path) -> Path | None:
    """自动查找后端目录。"""
    candidates = [
        project_dir / "backend",
        project_dir,
    ]
    for candidate in candidates:
        if (candidate / "app").exists():
            return candidate
    return None


def _check_init_exports(backend_dir: Path) -> AuditResult:
    """检查 __init__.py 导出是否完整。"""
    init_file = backend_dir / "app" / "common" / "retrieval" / "__init__.py"
    if not init_file.exists():
        return AuditResult(
            "__init__.py导出完整",
            "FAIL",
            "未找到 __init__.py",
        )

    content = init_file.read_text(encoding="utf-8")
    missing = []
    for name in EXPECTED_EXPORTS:
        if name not in content:
            missing.append(name)

    if not missing:
        return AuditResult(
            "__init__.py导出完整",
            "PASS",
            f"所有必要导出均存在: {', '.join(EXPECTED_EXPORTS)}",
        )
    return AuditResult(
        "__init__.py导出完整",
        "FAIL",
        f"缺少导出: {', '.join(missing)}",
    )


def _check_import_usage(backend_dir: Path) -> AuditResult:
    """检查业务代码是否正确导入（从 app.common.retrieval 导入）。"""
    correct_pattern = re.compile(r"from\s+app\.common\.retrieval")
    old_pattern = re.compile(r"from\s+app\.clients\.literature_search_client")

    correct_count = 0
    old_count = 0
    files_checked = 0

    for py_file in backend_dir.rglob("*.py"):
        # 跳过检索模块自身和兼容层
        rel_path = py_file.relative_to(backend_dir)
        rel = rel_path.as_posix()
        if rel.startswith("app/common/retrieval/") or rel == "app/clients/literature_search_client.py":
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        files_checked += 1
        if correct_pattern.search(content):
            correct_count += 1
        if old_pattern.search(content):
            old_count += 1

    if files_checked == 0:
        return AuditResult(
            "业务代码正确导入",
            "WARN",
            "未找到可检查的 Python 文件",
        )

    if old_count > 0:
        return AuditResult(
            "业务代码正确导入",
            "WARN",
            f"发现 {old_count} 个文件仍使用旧导入路径 (app.clients.literature_search_client)",
        )

    if correct_count > 0:
        return AuditResult(
            "业务代码正确导入",
            "PASS",
            f"有 {correct_count} 个文件使用正确导入路径",
        )

    return AuditResult(
        "业务代码正确导入",
        "WARN",
        "未发现任何检索相关导入，可能尚未集成",
    )


def _check_forbidden_calls(backend_dir: Path) -> AuditResult:
    """检查是否存在直接 Milvus/Solr/Reranker 调用。"""
    violations: list[str] = []

    for py_file in backend_dir.rglob("*.py"):
        rel = py_file.relative_to(backend_dir).as_posix()
        # 跳过检索模块自身（模块内部可能引用这些词）
        if rel.startswith("app/common/retrieval/"):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        for pattern in FORBIDDEN_DIRECT_CALLS:
            if re.search(pattern, content):
                violations.append(f"{rel}: 匹配 {pattern}")

    if not violations:
        return AuditResult(
            "无直接Milvus/Solr/Reranker调用",
            "PASS",
            "未发现直接底层调用",
        )
    return AuditResult(
        "无直接Milvus/Solr/Reranker调用",
        "FAIL",
        f"发现 {len(violations)} 处直接调用: {'; '.join(violations[:5])}",
    )


def _check_adapter_exists(backend_dir: Path) -> AuditResult:
    """检查 Adapter 文件是否存在。"""
    adapters_dir = backend_dir / "app" / "common" / "retrieval" / "adapters"
    if not adapters_dir.exists():
        return AuditResult(
            "Adapter文件存在",
            "FAIL",
            "adapters/ 目录不存在",
        )

    adapter_files = [
        f for f in adapters_dir.iterdir()
        if f.is_file() and f.suffix == ".py" and f.name != "__init__.py"
    ]

    if not adapter_files:
        return AuditResult(
            "Adapter文件存在",
            "WARN",
            "adapters/ 目录下无业务适配器（仅有 __init__.py）",
        )
    return AuditResult(
        "Adapter文件存在",
        "PASS",
        f"找到 {len(adapter_files)} 个适配器: {', '.join(f.name for f in adapter_files)}",
    )


def _check_env_vars(project_dir: Path) -> AuditResult:
    """检查文献检索网关环境变量是否配置。"""
    env_files = [
        project_dir / ".env",
        project_dir / "backend" / ".env",
        project_dir / ".env.local",
        project_dir / "backend" / ".env.local",
    ]

    for env_file in env_files:
        if not env_file.exists():
            continue
        try:
            content = env_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        for env_name in ENV_URL_NAMES:
            if env_name not in content:
                continue
            for line in content.splitlines():
                line = line.strip().lstrip("\ufeff")
                if line.startswith(env_name):
                    value = line.split("=", 1)[1].strip() if "=" in line else ""
                    if value and not value.startswith("#"):
                        return AuditResult(
                            "检索网关地址配置",
                            "PASS",
                            f"已在 {env_file.name} 中配置 {env_name}",
                        )
                    return AuditResult(
                        "检索网关地址配置",
                        "WARN",
                        f"已在 {env_file.name} 中声明 {env_name} 但值为空",
                    )

    return AuditResult(
        "检索网关地址配置",
        "FAIL",
        "未在任何 .env 文件中找到 LITERATURE_SEARCH_API_URL 或兼容变量",
    )


def _check_search_trace(backend_dir: Path) -> AuditResult:
    """检查 search_trace 是否可保存/返回。"""
    retrieval_dir = backend_dir / "app" / "common" / "retrieval"
    if not retrieval_dir.exists():
        return AuditResult(
            "search_trace可保存/返回",
            "FAIL",
            "retrieval 目录不存在",
        )

    # 检查 schemas.py 中是否定义了 SearchTrace
    schemas_file = retrieval_dir / "schemas.py"
    if not schemas_file.exists():
        return AuditResult(
            "search_trace可保存/返回",
            "FAIL",
            "schemas.py 不存在",
        )

    content = schemas_file.read_text(encoding="utf-8")
    if "SearchTrace" not in content:
        return AuditResult(
            "search_trace可保存/返回",
            "FAIL",
            "schemas.py 中未定义 SearchTrace",
        )

    # 检查 client.py 中是否使用 SearchTrace
    client_file = retrieval_dir / "client.py"
    if client_file.exists():
        client_content = client_file.read_text(encoding="utf-8")
        if "SearchTrace" in client_content and "search_trace" in client_content:
            return AuditResult(
                "search_trace可保存/返回",
                "PASS",
                "SearchTrace 已定义并在 client.py 中使用",
            )

    return AuditResult(
        "search_trace可保存/返回",
        "WARN",
        "SearchTrace 已定义但未在 client.py 中使用",
    )


def main() -> None:
    args = _parse_args()
    project_dir: Path = args.project_dir.resolve()
    output: Path | None = args.output

    if not project_dir.exists():
        print(f"[错误] 项目目录不存在: {project_dir}")
        sys.exit(1)

    # 自动查找后端目录
    backend_dir = _find_backend_dir(project_dir)
    if backend_dir is None:
        print(f"[错误] 未找到后端目录（需包含 app/ 子目录）: {project_dir}")
        sys.exit(1)

    print(f"项目目录: {project_dir}")
    print(f"后端目录: {backend_dir}")
    print()

    # ---- 执行审计检查 ----
    results: list[AuditResult] = [
        _check_retrieval_dir(project_dir),
        _check_init_exports(backend_dir),
        _check_import_usage(backend_dir),
        _check_forbidden_calls(backend_dir),
        _check_adapter_exists(backend_dir),
        _check_env_vars(project_dir),
        _check_search_trace(backend_dir),
    ]

    # ---- 输出审计结果 ----
    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    warn_count = sum(1 for r in results if r.status == "WARN")

    for r in results:
        icon = {"PASS": "OK", "FAIL": "FAIL", "WARN": "WARN"}[r.status]
        print(f"  [{icon}] {r.name}: {r.status}")
        if r.detail:
            print(f"      {r.detail}")

    # ---- 审计摘要 ----
    print()
    print("=" * 60)
    print("审计摘要")
    print("=" * 60)
    print(f"  PASS: {pass_count}")
    print(f"  FAIL: {fail_count}")
    print(f"  WARN: {warn_count}")
    print(f"  总计: {len(results)}")

    if fail_count > 0:
        print("\n  状态: 不通过")
    elif warn_count > 0:
        print("\n  状态: 有警告")
    else:
        print("\n  状态: 通过")

    # ---- 输出JSON报告 ----
    if output:
        report = {
            "project_dir": str(project_dir),
            "backend_dir": str(backend_dir),
            "results": [r.to_dict() for r in results],
            "summary": {
                "pass": pass_count,
                "fail": fail_count,
                "warn": warn_count,
                "total": len(results),
                "status": "pass" if fail_count == 0 and warn_count == 0
                else "warn" if fail_count == 0
                else "fail",
            },
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已输出到: {output}")

    # 非零退出码标记不通过
    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
