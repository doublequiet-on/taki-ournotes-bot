"""Capture and compare synthetic group turns through the chat provider boundary."""
from argparse import ArgumentParser
import ast
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
from random import SystemRandom
import subprocess
import sys
import unittest
from unittest.mock import patch

from replay import call, read, save


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = "2cfaa7087b60e067066250862195c276a5e7bec6"
VARIANTS = ("baseline", "revised")
FIXTURE_PROFILE = dict(provider="openai", wire_profile="openai-responses-v1",
                       model="gpt-6.1-terra", reasoning_effort="medium",
                       max_input_tokens=32768, max_output_tokens=1024)


def base_style():
    source = subprocess.check_output(
        ["git", "show", f"{BASE}:src/ournotes_bot/application/persona.py"], cwd=ROOT,
    ).decode("utf-8")
    return next(ast.literal_eval(node.value) for node in ast.parse(source).body
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "REPLY_STYLE"
                    for target in node.targets))


def capture(cases, profiles, story=None):
    sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "tests")]
    from ournotes_bot.application import chat_agent
    from test_console_policies import ChatServiceIntegrationTests

    class CaptureCase(ChatServiceIntegrationTests):
        async def asyncSetUp(self):
            with patch("time.time", return_value=1_795_348_800.0):
                await super().asyncSetUp()

        async def capture_turn(self):
            self.agent.context.clock = self.agent.store.clock = self.backend.clock
            self.enable_lightweight_fact_fixture()
            mode = self.scene["mode"]
            profile = profiles["addressed" if mode == "addressed" else "proactive"]
            self.snapshot = replace(self.snapshot, profile=replace(self.snapshot.profile, **profile))
            self.agent.story = story
            self.agent.proactive.one_pass_groups = frozenset({"g1"} if mode == "one_pass" else ())
            messages = self.scene["messages"]
            if mode == "addressed" and len(messages) == 3:
                self.client.request.return_value = self.result(messages[1][1])
                prior, _ = await self.prepare_native("/聊 " + messages[0][1])
                self.event(prior, "intent")
                self.event(prior, "ack")
                self.now += 1

            async def respond(profile, body, **_):
                schema = body.get("text", {}).get("format", {}).get("name")
                phase = {"proactive_participation": "decision", "proactive_one_pass": "combined"}.get(
                    schema, "addressed" if mode == "addressed" else "generation",
                )
                self.wire.append({"phase": phase, "body": deepcopy(body),
                                  "input_limit": profile.max_input_tokens})
                if phase == "decision":
                    return self.result('{"participation":"reply","text":"","delay_seconds":null}')
                if phase == "combined":
                    # This response only advances the fixture; its wording is not evidence.
                    return self.result(json.dumps(dict(
                        participation="skip", text="", delay_seconds=None, target_message_id=None,
                        anchor_message_ids=[], reply_kind=None,
                    )))
                return self.result("合成夹具已到达正文阶段")

            self.client.request.side_effect = respond
            if mode == "addressed":
                plan, _ = await self.prepare_native("/聊 " + messages[-1][1], seq=len(messages))
                self.assertTrue(plan["send_allowed"], plan)
            else:
                for seq, (speaker, text) in enumerate(messages, 1):
                    self.current_user = speaker
                    value = self.observation(text, seq=seq)
                    self.now += 1
                self.tick += 1.6
                plan, _, _ = await self.candidate(value)
                self.assertEqual(bool(plan["parts"]), mode == "staged", plan)

    styles = dict(zip(VARIANTS, (base_style(), chat_agent.REPLY_STYLE)))
    assert styles["baseline"] != styles["revised"]
    captured = []
    for scene in cases:
        wires = {}
        for variant, style in styles.items():
            fixture = CaptureCase("capture_turn")
            fixture.scene, fixture.wire = scene, []
            with patch.object(chat_agent, "REPLY_STYLE", style), patch.dict(
                os.environ, {"OURNOTES_ALIAS_FILE": str(ROOT / "query_aliases.json")},
            ):
                result = fixture.run(unittest.TestResult())
            if not result.wasSuccessful():
                raise RuntimeError(f"{scene['id']}/{variant}: {result.errors + result.failures}")
            wires[variant] = fixture.wire
        captured.append({"id": scene["id"], "wires": wires})
    return captured, styles


def verify(cases, captured, styles, profiles, approved_prompt=None):
    phases = {"addressed": ["addressed"], "one_pass": ["combined"], "staged": ["decision", "generation"]}
    for scene, turn in zip(cases, captured, strict=True):
        assert scene["id"] == turn["id"]
        baseline, revised = (turn["wires"][variant] for variant in VARIANTS)
        assert [row["phase"] for row in baseline] == phases[scene["mode"]]
        for before, after in zip(baseline, revised, strict=True):
            body = before["body"]
            profile = profiles["addressed" if scene["mode"] == "addressed" else "proactive"]
            assert body["model"] == profile["model"] and body["store"] is False and body["stream"] is False
            effort = profile["reasoning_effort"]
            assert body.get("reasoning") == (None if effort is None else {"effort": effort})
            output_cap = {"decision": 768, "generation": 1024, "combined": 1024}.get(
                before["phase"], profile["max_output_tokens"],
            )
            assert body["max_output_tokens"] == min(profile["max_output_tokens"], output_cap)
            if approved_prompt is not None:
                assert "人工批准人格档案：\n" + approved_prompt in body["input"][0]["content"]
            message_rows = [json.loads(row["content"]) for row in body["input"] if row["role"] in ("user", "assistant")]
            assert [(row.get("speaker_id") if row["role"] == "user" else "Taki", row["text"])
                    for row in message_rows] == [tuple(pair) for pair in scene["messages"]], (scene["id"], message_rows)
            normalized = deepcopy(after)
            if before["phase"] != "decision":
                prompt = normalized["body"]["input"][0]["content"]
                assert prompt.count(styles["revised"]) == 1
                normalized["body"]["input"][0]["content"] = prompt.replace(styles["revised"], styles["baseline"], 1)
            assert normalized == before, (scene["id"], before["phase"])


def plan(captured, count):
    jobs = []
    rng = SystemRandom()
    for turn in captured[:count]:
        order = list(VARIANTS)
        rng.shuffle(order)
        for letter, variant in zip(("A", "B"), order, strict=True):
            # Compare conditional generation for staged turns; the shared decision
            # is captured locally and never inserted into the generation input.
            row = turn["wires"][variant][-1]
            jobs.append({**row, "id": turn["id"], "letter": letter, "variant": variant})
    return jobs


def display(row, text):
    from ournotes_bot.application.chat_proactive_runtime import ProactiveChat, _echo_key
    from ournotes_bot.domain.model_calls import ModelError

    if row["phase"] == "combined":
        messages = [json.loads(item["content"]) for item in row["body"]["input"][1:]]
        message_ids = [item["message_id"] for item in messages]
        answer = ProactiveChat.parse_one_pass(text, message_ids[-1], set(message_ids[:-1]))
        if answer["participation"] != "reply":
            return {"skip": "（静默）", "defer": "（暂缓）"}[answer["participation"]]
        text = answer["text"]
    if row["phase"] in ("combined", "generation"):
        current = json.loads(row["body"]["input"][-1]["content"])["text"]
        if row["phase"] == "generation" and (len(text) > 90 or "<TAKI_SKIP>" in text and text != "<TAKI_SKIP>"):
            raise ModelError("model_protocol_error")
        echo = _echo_key(text)
        if echo and echo == _echo_key(current):
            return "（静默）"
    return "（静默）" if row["phase"] == "generation" and text == "<TAKI_SKIP>" else text


def blind(cases, completed, out):
    lines = ["# Taki 群聊盲评", "",
             "每题选 A、B、并列或都不合适，并写一句具体理由。"
             "兼顾上下文和交流对象、群聊自然度、立希的分寸和用户实际要求。"
             "合适的静默和短回复都可胜出。", ""]
    pairs = {}
    for scene in cases:
        cid = scene["id"]
        if not {"A", "B"} <= completed.get(cid, {}).keys():
            continue
        replies = completed[cid]
        pairs[cid] = {letter: replies[letter]["variant"] for letter in ("A", "B")}
        lines.extend([f"## {cid}", "", f"本题观察点：{scene['criterion']}", ""])
        if scene["mode"] == "staged":
            lines.extend(["本题已进入正文阶段。", ""])
        lines.extend(f"> {speaker}：{text}" for speaker, text in scene["messages"])
        for letter in ("A", "B"):
            text = replies[letter]["display"]
            lines.extend(["", f"**{letter}**", "", "> " + text.replace("\n", "\n> ")])
        lines.extend(["", "选择：____　理由：____", ""])
    (out / "盲评样卷.md").write_text("\n".join(lines), encoding="utf-8")
    save(out / "blind-mapping.json", pairs)


def send(cases, jobs, out, key):
    from ournotes_bot.domain.model_calls import ModelError

    completed = {}
    with (out / "attempts.jsonl").open("x", encoding="utf-8", buffering=1) as journal:
        for job in jobs:
            label = f"{job['id']}/{job['letter']}"
            body = job["body"]
            request_hash = sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            journal.write(json.dumps({"request": label, "sha256": request_hash}) + "\n")
            result = call(key, "generation", label, body, prose_only=True)
            journal.write(json.dumps({"result": label, **result}, ensure_ascii=False) + "\n")
            if result["status"] == "tool_or_action":
                continue
            if result["status"] != "ok":
                print(f"Stopped at {label}: {result['status']}. The attempt may be billable.")
                break
            try:
                rendered = display(job, result["text"])
            except (ModelError, ValueError):
                journal.write(json.dumps({"request": label, "status": "invalid_output"}) + "\n")
                continue
            completed.setdefault(job["id"], {})[job["letter"]] = {"variant": job["variant"], "display": rendered}
    blind(cases, completed, out)


def runtime_snapshot(path, story_root):
    doc = read(path)
    if set(doc) != {"runtime_revision", "chat_engine", "approved_persona", "proactive_mode", "profiles"}:
        raise ValueError("runtime snapshot fields mismatch")
    if (doc["chat_engine"] != "agent" or doc["proactive_mode"] != "one_pass"
            or doc["runtime_revision"] != BASE):
        raise ValueError("only the pinned Agent base with actual one-pass proactive chat is supported")
    profiles = doc["profiles"]
    if set(profiles) != {"addressed", "proactive"}:
        raise ValueError("runtime snapshot must give the selected explicit and proactive profiles")
    for profile in profiles.values():
        if set(profile) != FIXTURE_PROFILE.keys() or (
            profile["provider"], profile["wire_profile"]
        ) != ("openai", "openai-responses-v1"):
            raise ValueError("only the official OpenAI Responses wire and documented parameters are supported")
    if doc["approved_persona"] is None:
        if story_root is not None:
            raise ValueError("persona package conflicts with the verified legacy fallback")
        return profiles, None, None
    if story_root is None:
        raise ValueError("the verified approved persona requires its isolated story package")
    sys.path.insert(0, str(ROOT / "src"))
    from ournotes_bot.application.persona import approved_voice_prompt
    from ournotes_bot.application.story_service import StoryService

    story = StoryService(story_root)
    persona, state = story.persona_selection()
    if state != "approved" or doc["approved_persona"] != {
        key: persona[key] for key in ("persona_sha256", "source_revision", "revocation_revision")
    }:
        raise ValueError("isolated approved persona does not match the runtime snapshot")
    return profiles, story, approved_voice_prompt(persona["prompt"])


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 11))
    parser.add_argument("--send", action="store_true", help="only after explicit model-call authorization")
    parser.add_argument("--runtime-snapshot", type=Path)
    parser.add_argument("--story-root", type=Path)
    parser.add_argument("--input-usd-per-million", type=Decimal)
    parser.add_argument("--output-usd-per-million", type=Decimal)
    parser.add_argument("--budget-usd", type=Decimal)
    args = parser.parse_args()
    rates = (args.input_usd_per_million, args.output_usd_per_million, args.budget_usd)
    key = os.environ.get("OPENAI_API_KEY") if args.send else None
    if args.send and (not args.runtime_snapshot or not key or
                     any(rate is None or not rate.is_finite() or rate <= 0 for rate in rates)):
        parser.error("--send requires a verified runtime snapshot, configured key, verified rates and a budget")
    if args.story_root and not args.runtime_snapshot:
        parser.error("--story-root requires --runtime-snapshot")
    cases = read(HERE / "style-turns.json")
    profiles = dict.fromkeys(("addressed", "proactive"), FIXTURE_PROFILE)
    story = approved_prompt = None
    if args.runtime_snapshot:
        profiles, story, approved_prompt = runtime_snapshot(args.runtime_snapshot, args.story_root)
        # Match the verified runtime mode for every proactive provider request.
        cases = [{**scene, "mode": "one_pass"} if scene["mode"] == "staged" else scene for scene in cases]
    captured, styles = capture(cases, profiles, story)
    verify(cases, captured, styles, profiles, approved_prompt)
    jobs = plan(captured, args.limit)
    maximum_input = sum(job["input_limit"] for job in jobs)
    maximum_output = sum(job["body"]["max_output_tokens"] for job in jobs)
    upper = None
    if args.send:
        input_rate, output_rate, budget = rates
        # The runtime reserves 25% additional input for these Responses families.
        reserve_input = sum(job["input_limit"] * (
            Decimal("1.25") if job["body"]["model"].startswith(("gpt-6", "gpt-5.6")) else 1
        ) for job in jobs)
        upper = (reserve_input * input_rate + maximum_output * output_rate) / 1_000_000
        if upper > budget:
            parser.error(f"reserved USD {upper} exceeds budget USD {budget}")
    args.out.mkdir(parents=True, exist_ok=False)
    save(args.out / "wire.json", {"base": BASE, "cases": captured})
    save(args.out / "manifest.json", {
        "base_style_source": BASE, "profiles": profiles, "offline_cases": len(cases),
        "runtime_snapshot_sha256": sha256(args.runtime_snapshot.read_bytes()).hexdigest() if args.runtime_snapshot else None,
        "offline_wire_count": sum(len(wire) for turn in captured for wire in turn["wires"].values()),
        "planned_requests": len(jobs), "input_bound": maximum_input, "output_bound": maximum_output,
        "reserved_usd": str(upper) if upper is not None else None, "mode": "provider" if args.send else "offline",
    })
    print(f"Verified {len(cases)} synthetic paired captures. Request plan: {len(jobs)} requests, "
          f"at most {maximum_input} input and {maximum_output} output tokens.")
    if args.send:
        send(cases, jobs, args.out, key)


if __name__ == "__main__":
    main()
