#!/usr/bin/env python3
"""Record every LP Ops model call live, then verify the cache.

Run from the repository root:
    SAVVY_LLM_MODE=live python3 scripts/record_lpo.py

Every call is forced with refresh=True, so this always reaches the model and
overwrites the recording. The script prints model, latency, and token usage for
each call and fails if any cache file is not marked "live model, recorded".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.lpo import data, logic
from app.platform import config

NOTE_PACKETS = ("P-02", "P-03")


def _print_call(label: str, result) -> tuple[int, int]:
    usage = result.usage or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    print(
        f"{label}: {result.model}, {result.latency_ms} ms, "
        f"{prompt_tokens} prompt + {completion_tokens} completion tokens"
    )
    return prompt_tokens, completion_tokens


def main() -> int:
    if config.llm_mode() != "live":
        print("SAVVY_LLM_MODE must be live. Run: SAVVY_LLM_MODE=live python3 scripts/record_lpo.py")
        return 1
    if not config.deepseek()["api_key"]:
        print("No DeepSeek key is configured. The recorder did not run.")
        return 1

    packets = data.packets()
    if len(packets) != 5:
        print(f"Expected 5 packets, found {len(packets)}. Run scripts/generate_lpo.py first.")
        return 1

    calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    parsed_extractions = {}
    parsed_checklists = {}

    for packet in packets:
        packet_id = str(packet["id"])
        print(f"Packet {packet_id}: {packet['title']}")
        try:
            extraction_result = logic.run_extraction(packet, refresh=True)
        except RuntimeError as exc:
            print(f"  extraction failed: {exc}")
            return 1
        calls += 1
        extraction_prompt_tokens, extraction_completion_tokens = _print_call("  extraction", extraction_result)
        prompt_tokens += extraction_prompt_tokens
        completion_tokens += extraction_completion_tokens
        extraction = logic.parse_extraction(extraction_result.text)
        parsed_extractions[packet_id] = extraction
        if extraction["ok"]:
            print(
                f"    parsed {len(extraction['fields'])} fields, "
                f"{len(extraction['exceptions'])} exceptions, "
                f"confidence {extraction['confidence']}"
            )
        else:
            print(f"    PARSE ERROR: {extraction['error']}")

        try:
            checklist_result = logic.run_checklist(packet, extraction, refresh=True)
        except RuntimeError as exc:
            print(f"  checklist failed: {exc}")
            return 1
        calls += 1
        checklist_prompt, checklist_completion = _print_call("  checklist", checklist_result)
        prompt_tokens += checklist_prompt
        completion_tokens += checklist_completion
        checklist = logic.parse_checklist(checklist_result.text)
        parsed_checklists[packet_id] = checklist
        if checklist["ok"]:
            print(f"    checklist status {checklist['status']}, {len(checklist['steps'])} items")
        else:
            print(f"    CHECKLIST PARSE ERROR: {checklist['error']}")

    for packet_id in NOTE_PACKETS:
        packet = next((item for item in packets if item["id"] == packet_id), None)
        if packet is None:
            print(f"No packet {packet_id}; skipped the status note.")
            continue
        try:
            note_result = logic.run_status_note(
                packet,
                parsed_extractions.get(packet_id),
                parsed_checklists.get(packet_id),
                refresh=True,
            )
        except RuntimeError as exc:
            print(f"  status note failed: {exc}")
            return 1
        calls += 1
        note_prompt, note_completion = _print_call(f"  status note {packet_id}", note_result)
        prompt_tokens += note_prompt
        completion_tokens += note_completion
        note = logic.parse_note(note_result.text)
        if note["ok"]:
            print(f"    note subject: {note['subject']}")
            print(f"    guardrail flags: {logic.note_flags(note['body']) or 'none'}")
        else:
            print(f"    NOTE PARSE ERROR: {note['error']}")

    expected_keys = [logic.extraction_key(str(item["id"])) for item in packets]
    expected_keys += [logic.checklist_key(str(item["id"])) for item in packets]
    expected_keys += [logic.note_key(packet_id) for packet_id in NOTE_PACKETS]

    print("")
    print(f"Calls made: {calls}")
    print(f"Tokens: {prompt_tokens} prompt, {completion_tokens} completion")
    print("Recordings:")
    ok = True
    for key in expected_keys:
        path = data.CACHE_DIR / f"{key}.json"
        if not path.exists():
            print(f"  {key}: MISSING")
            ok = False
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        usage = payload.get("usage") or {}
        print(
            f"  {path.name}: {payload.get('mode')}, {payload.get('model')}, "
            f"{payload.get('latency_ms')} ms, {usage.get('prompt_tokens', 0)} + "
            f"{usage.get('completion_tokens', 0)} tokens"
        )
        if payload.get("mode") != "live model, recorded":
            ok = False
    print("Verification:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
