#!/usr/bin/env python3
"""Nova 2.5 Sonic checks for the session host.

1. End of speech -> final USER transcript: an Amazon Polly sentence (16 kHz PCM)
   is streamed at real-time pace, followed by silence, through the same stack
   the session host uses (Strands BidiAgent + BedrockNovaSonicModel). Reports the
   time from the last speech frame to the user transcript block, and to the
   first assistant output.
2. Concurrency: opens two Sonic streams at the same time from one process and
   checks both transcribe (make-before-break continuation needs this).

Usage: python3 sonic-check.py [--runs 5] [--model-id amazon.nova-2-5-sonic] [--region us-east-1]
Needs: strands-agents[bidi], boto3, and Bedrock access to the Sonic model.
"""

import argparse
import asyncio
import json
import statistics
import time

import boto3
from strands.bidi import BidiAgent
from strands.bidi.models import BedrockNovaSonicModel

SENTENCES = [
    "I'm looking for a leather bag for my mom, under eighty dollars.",
    "Show me something in purple.",
    "The second one on the left.",
    "Add it to my cart, please.",
    "Actually, a gift for my dad. Maybe a watch.",
]
FRAME_SAMPLES = 1024           # 64 ms at 16 kHz, like the browser's AudioWorklet
FRAME_BYTES = FRAME_SAMPLES * 2
SILENCE_S = 4.0
SYSTEM_PROMPT = "You are a shopping assistant. Reply with one short sentence."


def polly_pcm(text: str, region: str) -> bytes:
    polly = boto3.client("polly", region_name=region)
    response = polly.synthesize_speech(Text=text, OutputFormat="pcm", SampleRate="16000", VoiceId="Joanna", Engine="neural")
    return response["AudioStream"].read()


async def one_turn(pcm: bytes, model_id: str, region: str, label: str, sensitivity: str | None = None) -> dict:
    # Passed through to Sonic's sessionStart event (turn-taking controllability).
    params = {"turnDetectionConfiguration": {"endpointingSensitivity": sensitivity}} if sensitivity else {}
    model = BedrockNovaSonicModel(region=region, model_id=model_id, params=params,
                                  audio={"input": {"sample_rate": 16000}, "output": {"sample_rate": 24000}})
    agent = BidiAgent(model=model, system_prompt=SYSTEM_PROMPT)
    await agent.start()
    marks: dict = {}
    transcript = {"user": "", "assistant": ""}
    done = asyncio.Event()

    async def receive():
        async for event in agent.receive():
            kind, role, now = event.get("type"), event.get("role"), time.perf_counter()
            if kind == "bidi_transcript_delta" and role == "user":
                marks.setdefault("first_user_delta", now)
            elif kind == "bidi_transcript_block":
                transcript[role] += event.get("transcript", "")
                marks.setdefault(f"{role}_block", now)
            elif kind in ("bidi_audio_delta", "bidi_text_delta") or (kind == "bidi_transcript_delta" and role == "assistant"):
                marks.setdefault("first_assistant_output", now)
            if "assistant_block" in marks and "user_block" in marks:
                done.set()

    receiver = asyncio.create_task(receive())
    try:
        frame_s = FRAME_SAMPLES / 16000
        start = time.perf_counter()
        frames = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]
        silence = [bytes(FRAME_BYTES)] * int(SILENCE_S / frame_s)
        for i, frame in enumerate(frames + silence):
            if i == len(frames):
                marks["end_of_speech"] = time.perf_counter()
            await agent.send({"audio_delta": {"format": "pcm", "source": {"bytes": frame}}})
            await asyncio.sleep(max(0.0, start + (i + 1) * frame_s - time.perf_counter()))  # real-time pace
        try:
            await asyncio.wait_for(done.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
    finally:
        await agent.stop()
        receiver.cancel()

    end = marks.get("end_of_speech")
    ms = lambda key: round((marks[key] - end) * 1000) if key in marks and end else None  # noqa: E731
    return {
        "label": label,
        "user_transcript": transcript["user"].strip(),
        "first_user_delta_after_eos_ms": ms("first_user_delta"),
        "final_user_transcript_after_eos_ms": ms("user_block"),
        "first_assistant_output_after_eos_ms": ms("first_assistant_output"),
    }


async def main_async(args) -> None:
    audio = [polly_pcm(s, args.region) for s in SENTENCES]
    runs = []
    for i in range(args.runs):
        result = await one_turn(audio[i % len(audio)], args.model_id, args.region, f"run {i + 1}", args.sensitivity)
        runs.append(result)
        print(json.dumps(result))

    pair = []
    if not args.skip_concurrency:
        print("\n--- concurrency: two streams at once ---")
        pair = await asyncio.gather(
            one_turn(audio[0], args.model_id, args.region, "stream A", args.sensitivity),
            one_turn(audio[1], args.model_id, args.region, "stream B", args.sensitivity),
            return_exceptions=True,
        )
    for result in pair:
        print(json.dumps(result if isinstance(result, dict) else {"error": repr(result)}))

    finals = [r["final_user_transcript_after_eos_ms"] for r in runs if r["final_user_transcript_after_eos_ms"] is not None]
    firsts = [r["first_assistant_output_after_eos_ms"] for r in runs if r["first_assistant_output_after_eos_ms"] is not None]
    if finals:
        print(f"\nfinal user transcript after end of speech: median {statistics.median(finals)} ms, "
              f"min {min(finals)}, max {max(finals)} (n={len(finals)})")
    if firsts:
        print(f"first assistant output after end of speech: median {statistics.median(firsts)} ms (n={len(firsts)})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--model-id", default="amazon.nova-2-5-sonic")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--sensitivity", choices=["HIGH", "MEDIUM", "LOW"], help="endpointingSensitivity (default: Sonic's own)")
    parser.add_argument("--skip-concurrency", action="store_true")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
