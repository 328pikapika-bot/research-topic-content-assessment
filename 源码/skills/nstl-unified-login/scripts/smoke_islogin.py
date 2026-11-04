# -*- coding: utf-8 -*-
"""
smoke_islogin.py — 冒烟测试NSTL isLogin接口

测试未登录状态：GET /api/service/nstl/web/vilidate/isLogin 应返回 code:-1
测试已登录状态：如果提供了cookie则验证 code:0
输出结果为结构化JSON
"""

import argparse
import json
import ssl
import sys
import textwrap
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def make_request(
    url: str,
    cookie: str = "",
    proxy: str = "",
    timeout: int = 10,
    verbose: bool = False,
) -> dict:
    """发送HTTP请求并返回结果字典。

    Returns:
        dict: {
            "url": str,
            "status_code": int | None,
            "response_body": str,
            "response_json": dict | None,
            "error": str | None,
            "elapsed_ms": float,
            "timestamp": str,
        }
    """
    result = {
        "url": url,
        "status_code": None,
        "response_body": "",
        "response_json": None,
        "error": None,
        "elapsed_ms": 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    headers = {
        "User-Agent": "nstl-smoke-test/1.0",
        "X-Requested-With": "XMLHttpRequest",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    if cookie:
        headers["Cookie"] = cookie

    req = urllib.request.Request(url, headers=headers, method="GET")

    # 代理处理
    handler_params: list[object] = []
    if proxy:
        proxy_handler = urllib.request.ProxyHandler({
            "http": proxy,
            "https": proxy,
        })
        handler_params.append(proxy_handler)
    else:
        # 不使用系统代理，避免被Windows系统代理干扰
        no_proxy_handler = urllib.request.ProxyHandler({})
        handler_params.append(no_proxy_handler)

    # 跳过SSL验证（本地开发环境可能使用自签名证书）
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
    handler_params.append(https_handler)

    opener = urllib.request.build_opener(*handler_params)

    if verbose:
        print(f"  请求URL: {url}")
        print(f"  请求头: {json.dumps(headers, indent=2, ensure_ascii=False)}")
        if proxy:
            print(f"  代理: {proxy}")
        print(f"  超时: {timeout}s")
        print("")

    start = datetime.now()
    try:
        with opener.open(req, timeout=timeout) as resp:
            result["status_code"] = resp.status
            body = resp.read().decode("utf-8", errors="replace")
            result["response_body"] = body
            try:
                result["response_json"] = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                pass
    except urllib.error.HTTPError as e:
        result["status_code"] = e.code
        result["error"] = f"HTTP {e.code}: {e.reason}"
        try:
            body = e.read().decode("utf-8", errors="replace")
            result["response_body"] = body
        except Exception:
            pass
    except urllib.error.URLError as e:
        result["error"] = f"URL Error: {e.reason}"
    except TimeoutError:
        result["error"] = f"请求超时 ({timeout}s)"
    except Exception as e:
        result["error"] = f"未知错误: {type(e).__name__}: {e}"

    elapsed = (datetime.now() - start).total_seconds() * 1000
    result["elapsed_ms"] = round(elapsed, 1)

    return result


# ---------------------------------------------------------------------------
# 测试逻辑
# ---------------------------------------------------------------------------


def test_unauthenticated(base_url: str, proxy: str, timeout: int, verbose: bool) -> dict:
    """测试未登录状态：GET isLogin 应返回 code:-1。"""
    url = f"{base_url.rstrip('/')}/api/service/nstl/web/vilidate/isLogin"

    if verbose:
        print("--- 测试未登录状态 ---")

    result = make_request(url, cookie="", proxy=proxy, timeout=timeout, verbose=verbose)

    # 判断测试结果
    test_passed = False
    test_detail = ""

    if result["error"]:
        test_detail = f"请求失败: {result['error']}"
    elif result["response_json"] is not None:
        code = result["response_json"].get("code")
        if code == -1 or code == "-1":
            test_passed = True
            test_detail = f"返回 code:{code}，未登录状态正确"
        elif code == 0 or code == "0":
            test_detail = f"返回 code:{code}，意外已登录（无Cookie情况下不应返回已登录）"
        else:
            test_detail = f"返回 code:{code}，非预期的返回码"
    else:
        test_detail = f"无法解析响应为JSON，状态码: {result['status_code']}"

    return {
        "test_name": "unauthenticated_isLogin",
        "description": "未登录状态测试：GET isLogin 应返回 code:-1",
        "passed": test_passed,
        "detail": test_detail,
        "request": result,
    }


def test_authenticated(base_url: str, cookie: str, proxy: str, timeout: int, verbose: bool) -> dict:
    """测试已登录状态：如果提供了cookie则验证 code:0。"""
    url = f"{base_url.rstrip('/')}/api/service/nstl/web/vilidate/isLogin"

    if verbose:
        print("--- 测试已登录状态 ---")

    result = make_request(url, cookie=cookie, proxy=proxy, timeout=timeout, verbose=verbose)

    test_passed = False
    test_detail = ""

    if result["error"]:
        test_detail = f"请求失败: {result['error']}"
    elif result["response_json"] is not None:
        code = result["response_json"].get("code")
        if code == 0 or code == "0":
            test_passed = True
            test_detail = f"返回 code:{code}，登录状态正确"
            # 检查是否有用户信息
            data = result["response_json"].get("data", {})
            if isinstance(data, dict):
                user_id = data.get("userId") or data.get("user_id") or data.get("id", "")
                if user_id:
                    test_detail += f"，用户ID: {user_id}"
        elif code == -1 or code == "-1":
            test_detail = f"返回 code:{code}，Cookie无效或已过期"
        else:
            test_detail = f"返回 code:{code}，非预期的返回码"
    else:
        test_detail = f"无法解析响应为JSON，状态码: {result['status_code']}"

    return {
        "test_name": "authenticated_isLogin",
        "description": "已登录状态测试：GET isLogin 应返回 code:0",
        "passed": test_passed,
        "detail": test_detail,
        "request": result,
    }


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------


def generate_report(results: list[dict], base_url: str) -> dict:
    """生成结构化JSON报告。"""
    executed = [r for r in results if r["passed"] is not None]
    passed_count = sum(1 for r in executed if r["passed"])
    total_count = len(executed)

    return {
        "report_type": "nstl_islogin_smoke_test",
        "base_url": base_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": total_count,
            "skipped": len(results) - total_count,
            "passed": passed_count,
            "failed": total_count - passed_count,
            "all_passed": passed_count == total_count,
        },
        "tests": results,
    }


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="冒烟测试NSTL isLogin接口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              python smoke_islogin.py
              python smoke_islogin.py --base-url https://myapp.nstl.gov.cn
              python smoke_islogin.py --cookie "JSESSIONID=xxx" --verbose
              python smoke_islogin.py --proxy http://127.0.0.1:7890
        """),
    )
    parser.add_argument(
        "--base-url",
        default="https://www.nstl.gov.cn",
        help="NSTL基础URL (默认: https://www.nstl.gov.cn)",
    )
    parser.add_argument("--cookie", default="", help="已登录Cookie字符串（用于测试已登录状态）")
    parser.add_argument("--proxy", default="", help="HTTP代理地址 (如 http://127.0.0.1:7890)")
    parser.add_argument("--timeout", type=int, default=10, help="请求超时秒数 (默认: 10)")
    parser.add_argument("--verbose", action="store_true", help="显示详细请求信息")
    parser.add_argument("--output", default="", help="将JSON结果输出到指定文件")

    args = parser.parse_args()

    results: list[dict] = []

    # 测试1：未登录状态
    unauth_result = test_unauthenticated(args.base_url, args.proxy, args.timeout, args.verbose)
    results.append(unauth_result)

    # 测试2：已登录状态（仅当提供了cookie时）
    if args.cookie:
        auth_result = test_authenticated(args.base_url, args.cookie, args.proxy, args.timeout, args.verbose)
        results.append(auth_result)
    else:
        if args.verbose:
            print("--- 跳过已登录状态测试（未提供 --cookie）---")
        results.append({
            "test_name": "authenticated_isLogin",
            "description": "已登录状态测试：GET isLogin 应返回 code:0",
            "passed": None,
            "detail": "跳过：未提供 --cookie 参数",
            "request": None,
        })

    # 生成报告
    report = generate_report(results, args.base_url)
    report_json = json.dumps(report, indent=2, ensure_ascii=False)

    print("")
    print("=" * 60)
    print("  NSTL isLogin 冒烟测试结果")
    print("=" * 60)
    for r in results:
        if r["passed"] is None:
            status = "SKIP"
        elif r["passed"]:
            status = "PASS"
        else:
            status = "FAIL"
        print(f"  [{status}] {r['test_name']}: {r['detail']}")
    print("")
    summary = report["summary"]
    print(f"  合计: {summary['total']} 项 | PASS: {summary['passed']} | FAIL: {summary['failed']}")
    print("=" * 60)

    # 输出完整JSON
    if args.verbose:
        print("")
        print("完整JSON报告:")
        print(report_json)

    # 保存到文件
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_json, encoding="utf-8")
        print(f"\n报告已保存到: {output_path}")

    # 返回码：全部通过返回0，否则返回1
    sys.exit(0 if summary["all_passed"] else 1)


if __name__ == "__main__":
    main()
