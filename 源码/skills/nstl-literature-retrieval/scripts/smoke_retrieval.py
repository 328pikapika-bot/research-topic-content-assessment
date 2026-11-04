# -*- coding: utf-8 -*-
"""冒烟测试文献检索网关。

构造最小化检索请求发送到网关，验证响应格式和字段完整性。

功能：
  - 构造最小化检索请求发送到网关
  - 验证响应格式：code==1, data 数组存在
  - 如果有结果，验证字段完整性（title, authors, year 等）
  - 输出结构化 JSON 报告
  - 使用 urllib（无第三方依赖），禁用系统代理

用法：
  python scripts/smoke_retrieval.py --api-url http://127.0.0.1:8765/search
  python scripts/smoke_retrieval.py --api-url http://127.0.0.1:8765/search --query "深度学习"
  python scripts/smoke_retrieval.py --api-url http://127.0.0.1:8765/search --expect-real --verbose
  python scripts/smoke_retrieval.py --api-url http://127.0.0.1:8765/search --output report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 检索结果中期望存在的字段
EXPECTED_PAPER_FIELDS = [
    "id", "title", "hasAuthor", "year", "hasSource",
    "doi", "hasFile", "language", "abstract", "score",
]

# 禁用系统代理处理函数
PROXY_HANDLER = urllib.request.ProxyHandler({})

ENV_URL_NAMES = [
    "LITERATURE_SEARCH_API_URL",
    "NSTL_RETRIEVAL_API_URL",
    "GOOD_QUESTION_MILVUS_API_URL",
    "MILVUS_API_URL",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="冒烟测试文献检索网关",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=None,
        help="检索网关地址（可从 LITERATURE_SEARCH_API_URL 或兼容环境变量读取）",
    )
    parser.add_argument(
        "--query",
        type=str,
        default="人工智能",
        help="测试查询文本（默认: 人工智能）",
    )
    parser.add_argument(
        "--paper-num",
        type=int,
        default=1,
        help="期望返回的文献数量（默认: 1）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="请求超时秒数（默认: 30）",
    )
    parser.add_argument(
        "--expect-real",
        action="store_true",
        default=False,
        help="期望真实结果，否则接受空结果",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="输出详细日志",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出报告文件路径（JSON格式）",
    )
    return parser.parse_args()


def _build_request_payload(query: str, paper_num: int) -> dict:
    """构造检索网关请求体。"""
    return {
        "question": f"{query} | 语种：中英混合 | 年份：2021-2026",
        "paperNum": paper_num,
        "rerank": True,
        "intent_classification": True,
        "solr_type": 2,
    }


def _read_api_url_from_env() -> str:
    """按优先级读取检索网关地址环境变量。"""
    for env_name in ENV_URL_NAMES:
        value = os.getenv(env_name)
        if value:
            return value
    return ""


def _send_request(
    api_url: str,
    payload: dict,
    timeout: int,
    verbose: bool,
) -> tuple[dict | None, str, float]:
    """发送检索请求，返回 (响应数据, 错误信息, 耗时秒数)。

    使用 urllib 并禁用系统代理，避免 Windows 代理拦截内网请求。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Source-Agent": "smoke_test",
    }

    req = urllib.request.Request(
        api_url,
        data=data,
        headers=headers,
        method="POST",
    )

    # 构建不使用代理的 opener
    opener = urllib.request.build_opener(PROXY_HANDLER)

    t0 = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            duration = time.perf_counter() - t0
            if verbose:
                print(f"[响应] HTTP {resp.status}")
                print(f"[响应] 耗时: {duration:.3f}s")
                print(f"[响应] 内容: {body[:500]}...")
            result = json.loads(body)
            if isinstance(result, str):
                result = json.loads(result)
            if not isinstance(result, dict):
                return None, f"响应JSON不是对象: {type(result).__name__}", duration
            return result, "", duration
    except urllib.error.HTTPError as exc:
        duration = time.perf_counter() - t0
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        error_msg = f"HTTP {exc.code}: {exc.reason}"
        if verbose:
            print(f"[错误] {error_msg}")
            print(f"[错误] 响应内容: {body[:300]}")
        return None, error_msg, duration
    except urllib.error.URLError as exc:
        duration = time.perf_counter() - t0
        error_msg = f"网络错误: {exc.reason}"
        if verbose:
            print(f"[错误] {error_msg}")
        return None, error_msg, duration
    except json.JSONDecodeError as exc:
        duration = time.perf_counter() - t0
        error_msg = f"响应JSON解析失败: {exc}"
        if verbose:
            print(f"[错误] {error_msg}")
        return None, error_msg, duration
    except Exception as exc:
        duration = time.perf_counter() - t0
        error_msg = f"未知错误: {exc}"
        if verbose:
            print(f"[错误] {error_msg}")
        return None, error_msg, duration


def _validate_response(
    response_data: dict,
    expect_real: bool,
    verbose: bool,
) -> list[dict]:
    """验证响应格式，返回验证结果列表。"""
    checks: list[dict] = []

    # 检查 code == 1
    code = response_data.get("code")
    if code == 1:
        checks.append({"name": "code==1", "status": "PASS", "detail": f"code={code}"})
    else:
        checks.append({"name": "code==1", "status": "FAIL", "detail": f"code={code}, 期望1"})

    # 检查 data 数组存在
    data = response_data.get("data")
    if isinstance(data, list):
        checks.append({"name": "data数组存在", "status": "PASS", "detail": f"返回 {len(data)} 条记录"})
    else:
        checks.append({"name": "data数组存在", "status": "FAIL", "detail": f"data 类型为 {type(data).__name__}"})
        return checks  # 无数据则不继续检查

    # 检查记录数
    if expect_real and len(data) == 0:
        checks.append({"name": "返回结果非空", "status": "FAIL", "detail": "期望真实结果但返回为空"})
    elif len(data) == 0:
        checks.append({"name": "返回结果非空", "status": "WARN", "detail": "返回为空（未使用 --expect-real，可接受）"})
    else:
        checks.append({"name": "返回结果非空", "status": "PASS", "detail": f"返回 {len(data)} 条记录"})

    # 检查字段完整性（仅第一条记录）
    if data:
        first = data[0]
        missing_fields = []
        for field in EXPECTED_PAPER_FIELDS:
            if field not in first:
                missing_fields.append(field)

        if not missing_fields:
            checks.append({"name": "字段完整性", "status": "PASS", "detail": "所有期望字段均存在"})
        else:
            checks.append({
                "name": "字段完整性",
                "status": "WARN",
                "detail": f"缺少字段: {', '.join(missing_fields)}",
            })

        # 检查 title 非空
        title = first.get("title", "")
        if title:
            checks.append({"name": "title非空", "status": "PASS", "detail": f"title={title[:50]}..."})
        else:
            checks.append({"name": "title非空", "status": "FAIL", "detail": "title 为空"})

        # 检查 year 合理
        year = first.get("year", 0)
        try:
            year_int = int(year)
            if 1900 <= year_int <= 2100:
                checks.append({"name": "year合理", "status": "PASS", "detail": f"year={year_int}"})
            else:
                checks.append({"name": "year合理", "status": "WARN", "detail": f"year={year_int} 不在合理范围"})
        except (TypeError, ValueError):
            checks.append({"name": "year合理", "status": "FAIL", "detail": f"year={year} 无法转换为整数"})

        if verbose:
            print(f"\n[首条记录] {json.dumps(first, ensure_ascii=False, indent=2)[:500]}")

    return checks


def main() -> None:
    args = _parse_args()

    # 确定 API URL
    api_url = args.api_url or _read_api_url_from_env()
    if not api_url:
        print("[错误] 未指定 API URL。请通过 --api-url 参数或 LITERATURE_SEARCH_API_URL 环境变量指定。")
        sys.exit(1)

    query = args.query
    paper_num = args.paper_num
    timeout = args.timeout
    expect_real = args.expect_real
    verbose = args.verbose
    output = args.output

    print(f"=== 文献检索冒烟测试 ===")
    print(f"  网关地址: {api_url}")
    print(f"  查询文本: {query}")
    print(f"  期望数量: {paper_num}")
    print(f"  超时设置: {timeout}s")
    print(f"  期望真实结果: {'是' if expect_real else '否'}")
    print()

    # ---- 构造并发送请求 ----
    payload = _build_request_payload(query, paper_num)
    if verbose:
        print(f"[请求] {json.dumps(payload, ensure_ascii=False)}")

    response_data, error_msg, duration = _send_request(api_url, payload, timeout, verbose)

    # ---- 处理请求失败 ----
    if response_data is None:
        print(f"\n[失败] 请求未成功: {error_msg}")
        print(f"  耗时: {duration:.3f}s")

        if output:
            report = {
                "success": False,
                "api_url": api_url,
                "query": query,
                "error": error_msg,
                "duration_s": round(duration, 3),
                "checks": [],
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n报告已输出到: {output}")
        sys.exit(1)

    # ---- 验证响应 ----
    checks = _validate_response(response_data, expect_real, verbose)

    # ---- 输出结果 ----
    print("\n--- 检查结果 ---")
    pass_count = sum(1 for c in checks if c["status"] == "PASS")
    fail_count = sum(1 for c in checks if c["status"] == "FAIL")
    warn_count = sum(1 for c in checks if c["status"] == "WARN")

    for c in checks:
        icon = {"PASS": "OK", "FAIL": "FAIL", "WARN": "WARN"}[c["status"]]
        print(f"  [{icon}] {c['name']}: {c['status']} - {c['detail']}")

    print(f"\n  耗时: {duration:.3f}s")
    print(f"  PASS: {pass_count}, FAIL: {fail_count}, WARN: {warn_count}")

    if fail_count > 0:
        print("\n  状态: 不通过")
    elif warn_count > 0:
        print("\n  状态: 有警告")
    else:
        print("\n  状态: 通过")

    # ---- 输出JSON报告 ----
    if output:
        report = {
            "success": fail_count == 0,
            "api_url": api_url,
            "query": query,
            "duration_s": round(duration, 3),
            "checks": checks,
            "summary": {
                "pass": pass_count,
                "fail": fail_count,
                "warn": warn_count,
            },
            "response_sample": response_data.get("data", [])[:2] if response_data.get("data") else [],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已输出到: {output}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
