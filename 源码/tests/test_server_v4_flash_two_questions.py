"""Test server_v4_flash with two research questions, collecting stage timing."""
import json
import time
import sys
from pathlib import Path
import requests

BASE = "http://127.0.0.1:17607"
HEADERS = {"X-User-Scope": "test_v4_flash", "Content-Type": "application/json"}

QUESTIONS = [
    {
        "user_input": "大语言模型在科研文献分析中有哪些应用与挑战？",
        "language_preference": "zh",
        "year_range": [2022, 2026],
    },
    {
        "user_input": "How can we reduce hallucination in large language models for medical question answering?",
        "language_preference": "en",
        "year_range": [2022, 2026],
    },
]


def submit_question(q):
    resp = requests.post(
        f"{BASE}/v1/content-assessment/run",
        json=q,
        headers=HEADERS,
        timeout=30,
    )
    data = resp.json()
    if data.get("success"):
        return data["run_id"]
    raise RuntimeError(f"Submit failed: {data}")


def poll_task(task_id, poll_interval=10, max_wait=900):
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > max_wait:
            raise TimeoutError(f"Task {task_id} timed out after {max_wait}s")

        resp = requests.get(
            f"{BASE}/v1/content-assessment/tasks/{task_id}",
            headers=HEADERS,
            timeout=30,
        )
        task = resp.json()
        status = task.get("status")
        stage = task.get("current_stage", "?")
        steps = task.get("completed_steps", 0)
        total = task.get("total_steps", 7)
        print(f"  [{elapsed:6.0f}s] status={status} stage={stage} ({steps}/{total})")

        if status in ("completed", "failed", "cancelled"):
            return task
        time.sleep(poll_interval)


def parse_stage_timing(task):
    stages = []
    for item in task.get("stage_trace", []):
        key = item["stage_key"]
        name = item["stage_name"]
        status = item["status"]
        started = item.get("started_at")
        completed = item.get("completed_at")
        duration = None
        if started and completed:
            from datetime import datetime
            try:
                s = datetime.fromisoformat(started)
                c = datetime.fromisoformat(completed)
                duration = round((c - s).total_seconds(), 1)
            except Exception:
                pass
        stages.append({
            "key": key,
            "name": name,
            "status": status,
            "duration_seconds": duration,
        })
    return stages


def parse_model_metrics(task):
    state = task.get("state", {})
    output_json = state.get("output_json", {}) or {}
    return output_json.get("model_call_metrics", {})


def parse_final_questions(task):
    state = task.get("state", {})
    result = []
    for q in (state.get("final_questions") or []):
        score_data = q.get("score", {})
        result.append({
            "question": q.get("question", ""),
            "score": score_data.get("total_score") if isinstance(score_data, dict) else score_data,
        })
    return result


def main():
    results = []
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n{'='*60}")
        print(f"Question {i}: {q['user_input'][:60]}...")
        print(f"{'='*60}")

        print("Submitting...")
        task_id = submit_question(q)
        print(f"Task ID: {task_id}")

        print("Polling...")
        task = poll_task(task_id)
        status = task.get("status")
        duration = task.get("duration_seconds")
        print(f"Final status: {status}, total duration: {duration}s")

        if status == "failed":
            print(f"Error: {task.get('error_message', 'N/A')[:300]}")

        stages = parse_stage_timing(task)
        metrics = parse_model_metrics(task)
        questions = parse_final_questions(task)

        print("\nStage timing:")
        for s in stages:
            d = s["duration_seconds"]
            d_str = f"{d:.1f}s" if d else "N/A"
            print(f"  {s['name']:20s} [{s['status']:9s}] {d_str:>8s}")

        if metrics:
            print("\nModel call summary:")
            for call_type, m in sorted(metrics.items()):
                if isinstance(m, dict):
                    dur = m.get("duration_seconds") or m.get("done_ms", 0) / 1000
                    model = m.get("model", "")
                    err = m.get("error", "")
                    tok = m.get("total_tokens", "")
                    status_flag = "OK" if not err else f"ERR: {err[:60]}"
                    print(f"  {call_type:30s} {dur:7.1f}s  model={model:20s}  {status_flag}")

        if questions:
            print("\nGenerated questions:")
            for j, fq in enumerate(questions[:3], 1):
                print(f"  Q{j}: [{fq['score']}] {fq['question'][:120]}")

        results.append({
            "question": q["user_input"],
            "status": status,
            "duration": duration,
            "stages": stages,
            "metrics": metrics,
            "questions": questions,
        })

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        print(f"\nQ: {r['question'][:60]}")
        print(f"Status: {r['status']}, Total: {r['duration']}s")
        print("Stage breakdown:")
        for s in r["stages"]:
            d = s["duration_seconds"]
            d_str = f"{d:.1f}s" if d else "N/A"
            print(f"  {s['name']:20s} {d_str:>8s}")

    # Save results
    out_path = ".tmp/live-tests/server_v4_flash_test_results.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    import os
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
