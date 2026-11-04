# -*- coding: utf-8 -*-
"""自动复制文献检索模块到目标项目。

将 nstl-literature-retrieval skill 的 assets/templates/ 下的检索模块
自动复制到目标项目后端目录，完成模块接入。

功能：
  - 将 assets/templates/backend/ 下的整个目录结构复制到目标项目
  - 复制兼容层（app/clients/literature_search_client.py）
  - 检查目标项目是否已有 app/common/retrieval/，提示冲突
  - 检查 pydantic、requests 等依赖是否在 pyproject.toml 中
  - 输出完成报告（已复制文件列表、需手动完成的步骤）

用法：
  python scripts/apply_retrieval.py --backend-dir /path/to/project/backend
  python scripts/apply_retrieval.py --backend-dir /path/to/project/backend --overwrite
  python scripts/apply_retrieval.py --backend-dir /path/to/project/backend --skip-compat
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SKILL_ROOT / "assets" / "templates"
BACKEND_TEMPLATES = TEMPLATES_DIR / "backend"

# 需要检查的 Python 依赖
REQUIRED_DEPS = ["pydantic", "requests"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="自动复制文献检索模块到目标项目后端目录",
    )
    parser.add_argument(
        "--backend-dir",
        required=True,
        type=Path,
        help="目标项目后端目录（如 /path/to/project/backend）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="是否覆盖已有文件（默认不覆盖，遇到冲突跳过）",
    )
    parser.add_argument(
        "--skip-compat",
        action="store_true",
        default=False,
        help="跳过兼容层（app/clients/literature_search_client.py）的复制",
    )
    return parser.parse_args()


def _copy_tree(
    src: Path,
    dst: Path,
    overwrite: bool,
    copied: list[str],
    skipped: list[str],
) -> None:
    """递归复制目录树，记录已复制和跳过的文件。"""
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(src)
        target = dst / relative
        if target.exists() and not overwrite:
            skipped.append(str(relative))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(item), str(target))
        copied.append(str(relative))


def _copy_single_file(
    src: Path,
    dst: Path,
    overwrite: bool,
    copied: list[str],
    skipped: list[str],
) -> None:
    """复制单个文件。"""
    if dst.exists() and not overwrite:
        skipped.append(str(dst.relative_to(dst.parents[2])))
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    copied.append(str(dst.relative_to(dst.parents[2])))


def _check_retrieval_conflict(backend_dir: Path) -> bool:
    """检查目标项目是否已有 app/common/retrieval/ 目录。"""
    retrieval_dir = backend_dir / "app" / "common" / "retrieval"
    return retrieval_dir.exists()


def _check_dependencies(backend_dir: Path) -> list[str]:
    """检查 pyproject.toml 中是否包含必要依赖。"""
    pyproject = backend_dir / "pyproject.toml"
    if not pyproject.exists():
        # 尝试项目根目录
        pyproject = backend_dir.parent / "pyproject.toml"
    if not pyproject.exists():
        return [f"未找到 pyproject.toml，无法检查依赖"]

    content = pyproject.read_text(encoding="utf-8")
    missing = []
    for dep in REQUIRED_DEPS:
        if dep not in content:
            missing.append(dep)
    return missing


def main() -> None:
    args = _parse_args()
    backend_dir: Path = args.backend_dir.resolve()
    overwrite: bool = args.overwrite
    skip_compat: bool = args.skip_compat

    # ---- 前置检查 ----
    if not BACKEND_TEMPLATES.exists():
        print(f"[错误] 模板目录不存在: {BACKEND_TEMPLATES}")
        sys.exit(1)

    if not backend_dir.exists():
        print(f"[错误] 目标后端目录不存在: {backend_dir}")
        sys.exit(1)

    if not (backend_dir / "app").exists():
        print(f"[警告] 目标目录下未找到 app/ 目录，请确认路径正确: {backend_dir}")

    # ---- 冲突检查 ----
    if _check_retrieval_conflict(backend_dir):
        if not overwrite:
            print("[冲突] 目标项目已存在 app/common/retrieval/ 目录！")
            print("       使用 --overwrite 参数强制覆盖，或手动处理冲突。")
            sys.exit(1)
        else:
            print("[警告] 目标项目已存在 app/common/retrieval/ 目录，将覆盖已有文件。")

    # ---- 复制检索模块 ----
    copied: list[str] = []
    skipped: list[str] = []

    print("\n=== 复制检索模块 ===")
    retrieval_src = BACKEND_TEMPLATES / "app" / "common" / "retrieval"
    retrieval_dst = backend_dir / "app" / "common" / "retrieval"

    if retrieval_src.exists():
        _copy_tree(retrieval_src, retrieval_dst, overwrite, copied, skipped)
        print(f"  源: {retrieval_src}")
        print(f"  目标: {retrieval_dst}")
    else:
        print(f"  [错误] 检索模块模板不存在: {retrieval_src}")
        sys.exit(1)

    # ---- 复制兼容层 ----
    if not skip_compat:
        print("\n=== 复制兼容层 ===")
        compat_src = BACKEND_TEMPLATES / "app" / "clients" / "literature_search_client.py"
        compat_dst = backend_dir / "app" / "clients" / "literature_search_client.py"

        if compat_src.exists():
            _copy_single_file(compat_src, compat_dst, overwrite, copied, skipped)
            print(f"  源: {compat_src}")
            print(f"  目标: {compat_dst}")
        else:
            print(f"  [跳过] 兼容层模板不存在: {compat_src}")
    else:
        print("\n=== 跳过兼容层（--skip-compat）===")

    # ---- 检查依赖 ----
    print("\n=== 依赖检查 ===")
    missing_deps = _check_dependencies(backend_dir)
    if missing_deps:
        for dep in missing_deps:
            print(f"  [缺失] {dep} 未在 pyproject.toml 中找到")
    else:
        print("  [OK] 所有必要依赖已配置")

    # ---- 完成报告 ----
    print("\n" + "=" * 60)
    print("完成报告")
    print("=" * 60)
    print(f"已复制文件: {len(copied)}")
    for f in copied:
        print(f"  [OK] {f}")

    if skipped:
        print(f"\n跳过文件（已存在且未使用 --overwrite）: {len(skipped)}")
        for f in skipped:
            print(f"  [SKIP] {f}")

    if missing_deps:
        print(f"\n需手动安装的依赖:")
        for dep in missing_deps:
            print(f"  pip install {dep}")

    print("\n需手动完成的步骤:")
    print("  1. 确认环境变量 LITERATURE_SEARCH_API_URL 已配置")
    print("  2. 确认环境变量 LITERATURE_SEARCH_TIMEOUT 已配置（可选，默认30秒）")
    print("  3. 在 app/common/retrieval/adapters/ 下创建业务适配器（参考 example.py）")
    if not skip_compat:
        print("  4. 确认兼容层导入路径（旧代码使用 app.clients.literature_search_client）")
    print("  5. 运行审计: python scripts/audit_retrieval.py --project-dir <your-project-root>")
    print("  6. 运行冒烟测试: python scripts/smoke_retrieval.py --api-url \"$LITERATURE_SEARCH_API_URL\" --query \"人工智能 科研问题\" --paper-num 1 --expect-real")


if __name__ == "__main__":
    main()
