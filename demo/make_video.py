"""Generate the ~60-90 s demo video from REAL recorded benchmark calls: rendered frames + macOS
text-to-speech narration + captions. No screen recording needed.

    uv run --with pillow --with imageio-ffmpeg python demo/make_video.py

Output: demo/out/servicing-bench-demo.mp4  (macOS only: uses `say` and `afconvert` for the voiceover)
"""

from __future__ import annotations

import json
import subprocess
import textwrap
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent.parent
OUT = Path(__file__).parent / "out"
RUN = ROOT / "runs" / "bench-20261005-125956"           # Haiku 4.5, both conditions, 4 trials
W, H, FPS = 1920, 1080, 24
VOICE, RATE = "Samantha", 182

# ── look ─────────────────────────────────────────────────────────────────────
BG, PANEL, BORDER = (15, 17, 21), (23, 26, 33), (48, 54, 66)
TEXT, DIM = (230, 232, 236), (125, 133, 144)
CALLER, AGENT = (121, 184, 255), (133, 232, 157)
WARN, WARN_BG = (255, 211, 61), (66, 54, 14)
BLOCK, BLOCK_BG = (249, 117, 131), (74, 22, 30)
OKC = (133, 232, 157)
MONO = "/System/Library/Fonts/Menlo.ttc"
SANS = "/System/Library/Fonts/HelveticaNeue.ttc"


def font(path, size, index=0):
    return ImageFont.truetype(path, size, index=index)


F_MONO, F_MONO_B = font(MONO, 21), font(MONO, 21, 1)
F_CAP = font(SANS, 38)
F_TITLE, F_SUB, F_BODY, F_SMALL = font(SANS, 96, 1), font(SANS, 44), font(SANS, 34), font(SANS, 26)


# ── narration: one clip per sentence, so captions stay in sync ──────────────
SEGMENTS = [
    ("title", [
        "I built a mortgage servicing AI agent, and a benchmark that tests whether it's safe enough for a lender.",
        "The question: can a cheap, fast model do this job, if the rules are enforced in code?",
    ]),
    ("payment", [
        "Here's a real recorded call. A simulated borrower calls in to make a payment.",
        "The agent verifies them, reads the payment back, and only takes it after a clear yes.",
    ]),
    ("compare", [
        "Now the same model and the same caller, twice. On the left, the rules are only in the prompt.",
        "The agent transfers the call without logging it. That's a compliance miss.",
        "On the right, a guardrail in code blocks that exact mistake. The agent writes the note, then transfers.",
        "It still made a different mistake, claiming to see a flag it never looked up. That's the next guardrail.",
    ]),
    ("scenarios", [
        "Sixteen scenarios: impostors, hardship, bankruptcy, and prompt injection, four runs each.",
        "Graded on the end state, and by an AI judge I checked against my own hand labels.",
    ]),
    ("results", [
        "The result: guardrails in code cut the fast model's violations in half, and made it far more consistent,",
        "at about forty percent of the big model's cost. The code and findings are on GitHub.",
    ]),
]
GAP, LEAD, TAIL = 0.35, 0.4, 0.8  # seconds


def synth(text: str, path: Path) -> float:
    aiff = path.with_suffix(".aiff")
    subprocess.run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@22050", "-c", "1", str(aiff), str(path)], check=True)
    aiff.unlink()
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def build_audio() -> tuple[list[dict], float]:
    """Returns a timeline of segments with per-sentence (start, end, text), and writes narration.wav."""
    timeline, t, frames = [], 0.0, []
    rate = 22050
    for i, (name, sentences) in enumerate(SEGMENTS):
        seg = {"name": name, "start": t, "sentences": []}
        t += LEAD
        frames.append(b"\x00\x00" * int(LEAD * rate))
        for j, s in enumerate(sentences):
            clip = OUT / f"s{i}_{j}.wav"
            dur = synth(s, clip)
            with wave.open(str(clip)) as w:
                frames.append(w.readframes(w.getnframes()))
            clip.unlink()
            seg["sentences"].append((t, t + dur, s))
            t += dur + GAP
            frames.append(b"\x00\x00" * int(GAP * rate))
        t += TAIL
        frames.append(b"\x00\x00" * int(TAIL * rate))
        seg["end"] = t
        timeline.append(seg)
    with wave.open(str(OUT / "narration.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(b"".join(frames))
    return timeline, t


# ── content from real traces ─────────────────────────────────────────────────
def call_lines(ep: Path, width: int) -> list[tuple[str, tuple, tuple | None, bool]]:
    """(text, color, background, bold) lines for one recorded call."""
    import sqlite3
    trace = [json.loads(l) for l in (ep / "trace.jsonl").read_text().splitlines() if l.strip()]
    audit = sqlite3.connect(ep / "episode.db").execute("SELECT tool, outcome, detail FROM tool_calls ORDER BY seq").fetchall()
    lines, pos = [], 0

    def add(prefix, text, color, bg=None, bold=False):
        text = text.replace("**", "").replace(" - ", " · ")  # Markdown the agent emitted; noise on screen
        body = textwrap.wrap(" ".join(text.split()), width - len(prefix)) or [""]
        for k, part in enumerate(body):
            lines.append(((prefix if k == 0 else " " * len(prefix)) + part, color, bg, bold))

    for e in trace:
        if e["event"] == "caller":
            add("Caller  ", e["text"], CALLER)
        elif e["event"] == "reply" and e["text"].strip():
            add("Agent   ", e["text"], AGENT)
        elif e["event"] == "tool":
            flagged = None
            while pos < len(audit):
                tool, outcome, detail = audit[pos]
                pos += 1
                if tool != e["name"]:
                    continue
                if outcome == "would_block":
                    flagged = detail
                    continue
                break
            if e["is_error"] and "BLOCKED BY POLICY" in e["output"]:
                add("  ", f"BLOCKED {e['name']}: call note required before transfer", BLOCK, BLOCK_BG, True)
            elif flagged:
                add("  ", f"VIOLATION (not blocked) {e['name']}: no call note before transfer", WARN, WARN_BG, True)
            else:
                add("  ", f"tool: {e['name']}", DIM)
    return lines


# ── drawing ──────────────────────────────────────────────────────────────────
def panel(d: ImageDraw.ImageDraw, box, title, title_color, lines, shown: int) -> None:
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, 18, fill=PANEL, outline=BORDER, width=2)
    d.text((x0 + 24, y0 + 16), title, font=F_MONO_B, fill=title_color)
    line_h = 30
    capacity = (y1 - y0 - 70) // line_h
    visible = lines[:shown][-capacity:]
    y = y0 + 62
    for text, color, bg, bold in visible:
        if bg:
            d.rectangle((x0 + 14, y - 3, x1 - 14, y + line_h - 5), fill=bg)
        d.text((x0 + 24, y), text, font=F_MONO_B if bold else F_MONO, fill=color)
        y += line_h


def caption(d: ImageDraw.ImageDraw, text: str) -> None:
    if not text:
        return
    wrapped = textwrap.wrap(text, 92)
    h = 30 + 50 * len(wrapped)
    d.rectangle((0, H - h - 10, W, H), fill=(0, 0, 0))
    for k, part in enumerate(wrapped):
        tw = d.textlength(part, font=F_CAP)
        d.text(((W - tw) / 2, H - h + 8 + 50 * k), part, font=F_CAP, fill=TEXT)


def centered(d, y, text, f, fill):
    d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill)


def reveal(t, start, end, n):
    """How many of n lines are visible at time t, revealing linearly between start and end."""
    if t <= start:
        return 0
    return n if t >= end else max(1, int(n * (t - start) / (end - start)))


def render(timeline, total, out_path):
    pay = call_lines(RUN / "pay-current-full" / "guardrails-t1", 120)
    left = call_lines(RUN / "servicemember-deployment" / "prompt_only-t4", 66)
    right = call_lines(RUN / "servicemember-deployment" / "guardrails-t3", 66)
    scen = sorted(p.stem.split("-", 1)[1] for p in (ROOT / "bench" / "scenarios").glob("*.yaml"))

    writer = imageio_ffmpeg.write_frames(str(out_path), (W, H), fps=FPS, quality=8, macro_block_size=8)
    writer.send(None)
    for f in range(int(total * FPS)):
        t = f / FPS
        seg = next(s for s in timeline if s["start"] <= t < s["end"] or s is timeline[-1])
        sents = seg["sentences"]
        cap = next((s[2] for s in sents if s[0] - 0.05 <= t < s[1] + GAP), "")
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)

        if seg["name"] == "title":
            centered(d, 300, "servicing-bench", F_TITLE, TEXT)
            centered(d, 440, "Can a fast, cheap AI model handle mortgage calls safely", F_SUB, DIM)
            centered(d, 500, "if the compliance rules are enforced in code?", F_SUB, DIM)
            centered(d, 640, "Real recorded calls  ·  Claude Haiku 4.5 and Claude Opus 5  ·  synthetic data", F_SMALL, DIM)
        elif seg["name"] == "payment":
            n = reveal(t, seg["start"] + 0.3, sents[-1][1] - 0.5, len(pay))
            panel(d, (60, 50, W - 60, H - 190), "pay-current-full  ·  guardrails on  ·  claude-haiku-4-5", TEXT, pay, n)
        elif seg["name"] == "compare":
            nl = reveal(t, sents[0][0], sents[1][1], len(left))
            nr = reveal(t, sents[2][0], sents[3][1], len(right))
            mid = W // 2
            panel(d, (40, 50, mid - 15, H - 190), "RULES IN PROMPT ONLY", WARN, left, nl)
            panel(d, (mid + 15, 50, W - 40, H - 190), "GUARDRAILS IN CODE", BLOCK, right, nr)
        elif seg["name"] == "scenarios":
            centered(d, 70, "16 scenarios  ×  2 conditions  ×  4 runs", F_SUB, TEXT)
            for k, name in enumerate(scen):
                col, row = k % 2, k // 2
                x, y = 360 + col * 640, 180 + row * 70
                shown = t >= seg["start"] + 0.3 + k * 0.25
                if shown:
                    d.rounded_rectangle((x, y, x + 600, y + 54), 12, fill=PANEL, outline=BORDER)
                    d.text((x + 22, y + 10), name.replace("-", " "), font=F_BODY, fill=TEXT)
            if t >= sents[-1][0]:
                centered(d, 770, "Graded on end state + an AI judge checked against 40 hand labels", F_BODY, DIM)
        else:  # results
            rows = [("", "Pass rate", "Pass all 4 runs", "Violations", "Cost / call"),
                    ("Haiku, rules in prompt", "83%", "56%", "16%", "$0.025"),
                    ("Haiku + code guardrails", "92%", "81%", "8%", "$0.026"),
                    ("Opus (either)", "100%", "100%", "0%", "$0.065")]
            centered(d, 90, "Results  ·  128 calls per model", F_SUB, TEXT)
            xs = [140, 760, 1050, 1390, 1640]
            for r, row in enumerate(rows):
                y = 230 + r * 110
                if r == 2:
                    d.rounded_rectangle((110, y - 20, W - 110, y + 70), 14, fill=(22, 48, 32), outline=OKC, width=2)
                for c, cell in enumerate(row):
                    f_ = F_SMALL if r == 0 else F_BODY
                    d.text((xs[c], y), cell, font=f_, fill=DIM if r == 0 else (OKC if r == 2 else TEXT))
            if t >= sents[-1][0]:
                centered(d, 760, "github.com/vfxdrummer/servicing-bench", F_SUB, CALLER)
        caption(d, cap)
        writer.send(img.tobytes())
    writer.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    timeline, total = build_audio()
    silent = OUT / "video_silent.mp4"
    render(timeline, total, silent)
    final = OUT / "servicing-bench-demo.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(silent),
                    "-i", str(OUT / "narration.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                    "-shortest", str(final)], check=True)
    silent.unlink()
    print(f"{final}  ({total:.0f} s)")


if __name__ == "__main__":
    main()
