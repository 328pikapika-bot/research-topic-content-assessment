"""Repair citation placement in the already published aspirin task."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.agents.topic_selection.presentation import candidate_reader_prose
from app.agents.topic_selection.schemas import TopicSelectionState
from app.core.config import MONGO_DB, MONGO_URI, TASK_COLLECTION
from app.services.topic_selection_report import build_topic_markdown

TASK_ID = "run_b4a5a9cbd0bf"
INLINE = re.compile(r"[（(]\s*(E-[A-Za-z0-9_-]+)\s*[）)]")
EMPTY = re.compile(r"[（(]\s*[）)]|\[\s*\]")
SOURCE_ACCURATE_WORDING = {
    "C2": ("现有文献分别报道了固体酸催化剂在间歇反应中的应用（E-b94537fb4afe）和连续流反应器在API合成中的应用（E-8b6209e224be）",
           "现有文献分别报道了磺酸功能化沉淀二氧化硅固体酸用于乙酰化反应（E-b94537fb4afe），以及串联连续搅拌釜用于聚乙二醇大环亚硫酸酯合成（E-8b6209e224be）"),
    "C1": ("现有文献分别报道了固体酸催化剂（E-8038808d5a3d）和绿色化学指标（E-9497b43d618c）",
           "现有文献分别报道了磷钼酸盐催化阿司匹林合成（E-8038808d5a3d），以及绿色酸催化用于芳基甲烷合成（E-9497b43d618c）"),
}


def repair(state: TopicSelectionState) -> dict[str, list[int]]:
    numbers = state.report_plan.citation_map
    sources = {item.evidence_id for item in state.evidence_pool}
    cited: dict[str, list[int]] = {}
    reasons: dict[str, str] = {}
    for candidate in state.candidates:
        innovation = candidate.innovation
        if candidate.candidate_id in SOURCE_ACCURATE_WORDING:
            original, corrected = SOURCE_ACCURATE_WORDING[candidate.candidate_id]
            if original not in innovation:
                raise ValueError(f"{candidate.candidate_id}: source assertion changed")
            innovation = innovation.replace(original, corrected)
        matches = INLINE.findall(innovation)
        if any(eid not in sources or eid not in numbers for eid in matches):
            raise ValueError(f"{candidate.candidate_id}: source absent from frozen bibliography")
        if matches:
            reason = INLINE.sub(lambda match: f"[{numbers[match[1]]}]", innovation)
            reasons[candidate.candidate_id] = candidate_reader_prose(reason)
            cited[candidate.candidate_id] = [numbers[eid] for eid in matches]
            content = candidate.reader_content or {}
            content["reason"] = {"text": candidate_reader_prose(innovation),
                                 "evidence_ids": list(dict.fromkeys(matches))}
            candidate.reader_content = content

    for sections in (state.report_plan.sections, state.report_snapshot.report_plan_snapshot.get("sections", [])):
        for section in sections:
            for block in section.get("blocks", []):
                if block.get("kind") != "candidate_detail":
                    continue
                for field in block.get("fields", []):
                    field["value"] = EMPTY.sub("", str(field.get("value") or ""))
                    if field.get("label") == "选题理由" and block.get("candidate_id") in reasons:
                        field["value"] = reasons[block["candidate_id"]]
    return cited


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    collection = MongoClient(MONGO_URI)[MONGO_DB][TASK_COLLECTION]
    row = collection.find_one({"task_id": TASK_ID})
    if not row or row.get("status") != "completed":
        raise RuntimeError("Target completed task not found")
    backup = ROOT / ".tmp" / "feedback1112-round3" / "aspirin-before-repair.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        backup.write_text(json.dumps(row, ensure_ascii=False, default=str), encoding="utf-8")
    state = TopicSelectionState.model_validate(row["state"])
    cited = repair(state)
    if not cited:
        raise RuntimeError("No verified inline citations found")
    state.output_markdown = build_topic_markdown(state)[0].decode("utf-8")
    print(json.dumps({"task_id": TASK_ID, "restored": cited,
                      "remaining_empty": len(EMPTY.findall(state.output_markdown))}, ensure_ascii=False))
    if "--apply" not in sys.argv:
        return
    result = collection.update_one(
        {"_id": row["_id"], "task_id": TASK_ID, "user_scope": row["user_scope"],
         "status": "completed", "updated_at": row["updated_at"]},
        {"$set": {"state": state.model_dump(), "output_markdown": state.output_markdown,
                  "updated_at": __import__("datetime").datetime.now().astimezone().isoformat()}},
    )
    if result.modified_count != 1:
        raise RuntimeError("Task changed before guarded update")


if __name__ == "__main__":
    main()
