"""Download authorized task exports for local report QA; never prints identity."""
import argparse
from pathlib import Path
import urllib.request

from pymongo import MongoClient
from app.core.config import MONGO_URI, MONGO_DB, TASK_COLLECTION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("output_dir")
    args = parser.parse_args()
    with MongoClient(MONGO_URI) as client:
        task = client[MONGO_DB][TASK_COLLECTION].find_one({"task_id": args.task_id}, {"user_scope": 1, "status": 1})
    if not task or task["status"] != "completed":
        raise RuntimeError("指定任务尚未完成")
    destination = Path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for format in ("pdf", "docx", "md", "zip"):
        suffix = "&artifact=evidence" if format == "zip" else ""
        request = urllib.request.Request(
            f"http://127.0.0.1:17607/v1/content-assessment/tasks/{args.task_id}/export?format={format}{suffix}",
            headers={"X-User-Scope": task["user_scope"]})
        with urllib.request.urlopen(request, timeout=180) as response:
            content = response.read()
        target = destination / ("evidence.zip" if format == "zip" else f"report.{format}")
        target.write_bytes(content)
        print(target.name, len(content), flush=True)


if __name__ == "__main__":
    main()
