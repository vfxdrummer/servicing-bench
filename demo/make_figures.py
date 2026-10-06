"""README figures, light + dark, from docs/results.json (the committed results snapshot), plus the hero GIF
(replayed from recorded runs, which must exist locally).

    uv run --with matplotlib --with pillow --with imageio-ffmpeg python demo/make_figures.py

Writes docs/img/*.png and docs/img/hero.gif. Colors: the dataviz reference palette (blue/orange slots,
validated for color-vision deficiency), sequential blue ramp for the heatmap.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).parent.parent
IMG = ROOT / "docs" / "img"
DATA = json.loads((ROOT / "docs" / "results.json").read_text())

for f in ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]:
    if Path(f).exists():
        font_manager.fontManager.addfont(f)
plt.rcParams.update({"font.family": ["Arial", "DejaVu Sans"], "font.size": 12, "svg.fonttype": "none"})

THEMES = {
    "light": dict(surface="#ffffff", ink="#0b0b0b", ink2="#52514e", muted="#8a8984", grid="#e6e5e1",
                  box="#f6f5f2", box_edge="#d6d4ce", s1="#2a78d6", s2="#eb6834",
                  seq=["#f3f7fd", "#cde2fb", "#86b6ef", "#3987e5", "#1c5cab"]),
    "dark": dict(surface="#0d1117", ink="#ffffff", ink2="#c3c2b7", muted="#8b8a84", grid="#2a2f37",
                 box="#161b22", box_edge="#30363d", s1="#3987e5", s2="#d95926",
                 seq=["#161b22", "#184f95", "#256abf", "#5598e7", "#9ec5f4"]),
}
LABEL = {"claude-haiku-4-5": "Haiku 4.5", "claude-opus-5": "Opus 5"}


def style(ax, t):
    ax.set_facecolor(t["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(t["grid"])
    ax.tick_params(colors=t["ink2"], labelsize=11, length=0, pad=6)
    ax.grid(True, color=t["grid"], linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)


def save(fig, name, theme):
    fig.savefig(IMG / f"{name}-{theme}.png", dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)


# ── 1. cost vs reliability ───────────────────────────────────────────────────
def cost_reliability(theme):
    t = THEMES[theme]
    c = DATA["conditions"]
    hp, hg = c["claude-haiku-4-5|prompt_only"], c["claude-haiku-4-5|guardrails"]
    op, og = c["claude-opus-5|prompt_only"], c["claude-opus-5|guardrails"]
    fig, ax = plt.subplots(figsize=(8, 4.6), facecolor=t["surface"])
    style(ax, t)
    x = lambda r: r["agent_cost_per_call"] * 100   # cents
    y = lambda r: r["pass_hat_4"] * 100
    ax.add_patch(FancyArrowPatch((x(hp), y(hp) + 3), (x(hg), y(hg) - 3.5), arrowstyle="-|>", mutation_scale=14,
                                 color=t["ink2"], linewidth=1.6, zorder=2))
    ax.text(x(hp) + 0.25, (y(hp) + y(hg)) / 2, f"+{y(hg) - y(hp):.0f} points\nwith guardrails in code",
            color=t["ink2"], fontsize=11, va="center")
    ax.scatter([x(hp)], [y(hp)], s=150, facecolors=t["surface"], edgecolors=t["s1"], linewidths=2.2, zorder=3)
    ax.scatter([x(hg)], [y(hg)], s=150, color=t["s1"], edgecolors=t["surface"], linewidths=2, zorder=3)
    opus_x = (x(op) + x(og)) / 2
    ax.scatter([opus_x], [y(op)], s=150, color=t["s2"], edgecolors=t["surface"], linewidths=2, zorder=3)
    ax.text(x(hp) + 0.25, y(hp), f"Haiku 4.5, rules in prompt only  ({y(hp):.0f}%)", color=t["ink"], va="center", fontsize=11.5)
    ax.text(x(hg) + 0.25, y(hg), f"Haiku 4.5 + guardrails  ({y(hg):.0f}%)", color=t["ink"], va="center", fontsize=11.5, weight="bold")
    ax.text(opus_x - 0.25, y(op) - 5.5, "Opus 5, with or without\nguardrails (100%)", color=t["ink"], ha="right", va="top", fontsize=11.5)
    ax.set_xlim(0, 8.5)
    ax.set_ylim(40, 108)
    ax.set_xlabel("Agent cost per call (cents)", color=t["ink2"], fontsize=11)
    ax.set_ylabel("Scenarios passed on all 4 runs (%)", color=t["ink2"], fontsize=11)
    fig.suptitle("Guardrails lifted the fast model 25 points, at the same cost",
                 x=0.07, y=0.97, ha="left", color=t["ink"], fontsize=14, weight="bold")
    fig.text(0.07, 0.885, "pass^4 vs. agent cost · 16 scenarios × 4 runs per condition · Opus ~2.5× the cost, ~2× the latency",
             color=t["muted"], fontsize=10.5)
    fig.subplots_adjust(left=0.1, right=0.97, top=0.83, bottom=0.13)
    save(fig, "cost-reliability", theme)


# ── 2. scenario heatmap ──────────────────────────────────────────────────────
def heatmap(theme):
    t = THEMES[theme]
    cols = [("claude-haiku-4-5|prompt_only", "Haiku\nprompt only"), ("claude-haiku-4-5|guardrails", "Haiku\n+ guardrails"),
            ("claude-opus-5|prompt_only", "Opus\nprompt only"), ("claude-opus-5|guardrails", "Opus\n+ guardrails")]
    order = ["routine_payment", "loan_info", "third_party", "hardship", "protected", "adversarial"]
    rows = sorted(DATA["scenarios"].items(), key=lambda kv: (order.index(kv[1]["category"]), kv[0]))
    cmap = LinearSegmentedColormap.from_list("seq", t["seq"])
    fig, ax = plt.subplots(figsize=(8, 8.4), facecolor=t["surface"])
    ax.set_facecolor(t["surface"])
    for i, (name, d) in enumerate(rows):
        for j, (key, _) in enumerate(cols):
            passed, n = d[key]
            failed = n - passed
            ax.add_patch(FancyBboxPatch((j + 0.05, i + 0.08), 0.9, 0.84, boxstyle="round,pad=0,rounding_size=0.08",
                                        facecolor=cmap(failed / 4), edgecolor="none"))
            dark_cell = failed >= 3  # only the darkest steps take light text; mid-blues keep dark ink
            ink = "#ffffff" if (dark_cell and theme == "light") else (t["surface"] if dark_cell else t["ink"])
            ax.text(j + 0.5, i + 0.5, f"{passed}/{n}", ha="center", va="center", fontsize=11, color=ink,
                    weight="bold" if failed else "normal")
    ax.set_xlim(0, len(cols))
    ax.set_ylim(len(rows), 0)
    ax.set_xticks([j + 0.5 for j in range(len(cols))], [c[1] for c in cols])
    ax.set_yticks([i + 0.5 for i in range(len(rows))], [n.replace("-", " ") for n, _ in rows])
    ax.xaxis.tick_top()
    ax.tick_params(colors=t["ink2"], labelsize=10.5, length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    # category separators
    prev = None
    for i, (_, d) in enumerate(rows):
        if prev and d["category"] != prev:
            ax.axhline(i, color=t["grid"], linewidth=1.2, xmin=-0.6, clip_on=False)
        prev = d["category"]
    fig.suptitle("Where the failures were", x=0.04, y=0.99, ha="left", color=t["ink"], fontsize=14, weight="bold")
    shade = "Darker" if theme == "light" else "Brighter"
    fig.text(0.04, 0.945, f"Runs passed out of 4, per scenario. {shade} = more failed runs.", color=t["muted"], fontsize=10.5)
    fig.subplots_adjust(left=0.36, right=0.98, top=0.85, bottom=0.02)
    save(fig, "scenarios", theme)


# ── 3. architecture ──────────────────────────────────────────────────────────
def architecture(theme):
    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=(10, 4.9), facecolor=t["surface"])
    ax.set_facecolor(t["surface"])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 50)
    ax.axis("off")

    def box(x, y, w, h, title, sub, chips=()):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.6",
                                    facecolor=t["box"], edgecolor=t["box_edge"], linewidth=1.2))
        ax.text(x + 2, y + h - 3.2, title, color=t["ink"], fontsize=12.5, weight="bold", va="top")
        ax.text(x + 2, y + h - 7.6, sub, color=t["ink2"], fontsize=10.5, va="top", linespacing=1.35)
        for k, chip in enumerate(chips):
            cy = y + 2.2 + k * 4.4
            ax.add_patch(FancyBboxPatch((x + 2, cy), w - 4, 3.4, boxstyle="round,pad=0,rounding_size=1.1",
                                        facecolor=t["s1"], edgecolor="none"))
            ax.text(x + 3.3, cy + 1.7, chip, color="#ffffff", fontsize=9.5, va="center", weight="bold")

    def arrow(x0, y0, x1, y1, label="", both=False):
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="<|-|>" if both else "-|>",
                                     mutation_scale=13, color=t["muted"], linewidth=1.4))
        if label:
            ax.text((x0 + x1) / 2, max(y0, y1) + 1.3, label, ha="center", color=t["muted"], fontsize=9.5)

    box(1, 25, 25, 22, "Simulated borrower", "Claude Sonnet 5 plays a\ncaller from a scenario:\npersona, goal, hidden facts")
    box(34, 13, 30, 34, "Servicing agent", "model under test (Haiku / Opus)\nsame 10-rule policy prompt\nin both conditions",
        chips=["post-call hook: write missing note", "output check: debt disclosure first"])
    box(72, 13, 27, 34, "Servicing system", "7 tools over MCP\nSQLite · 50 synthetic loans\naudit log of every call",
        chips=["no transfer without a note", "payment limits", "verify before disclosing"])
    arrow(26, 36, 34, 36, "conversation", both=True)
    arrow(64, 30, 72, 30, "tool calls", both=True)
    ax.add_patch(FancyBboxPatch((1, 1), 98, 8.5, boxstyle="round,pad=0,rounding_size=1.6",
                                facecolor=t["surface"], edgecolor=t["box_edge"], linewidth=1.2, linestyle=(0, (3, 2))))
    ax.text(3, 5.2, "Grader", color=t["ink"], fontsize=12.5, weight="bold", va="center")
    ax.text(13, 5.2, "end state (payments, tasks, transfer, note)  +  audit log  +  LLM judge, checked against 40 hand labels",
            color=t["ink2"], fontsize=10.5, va="center")
    ax.add_patch(FancyBboxPatch((63.5, 47.6), 2.4, 1.6, boxstyle="round,pad=0,rounding_size=0.5",
                                facecolor=t["s1"], edgecolor="none"))
    ax.text(66.6, 48.4, "= guardrail enforced in code (guardrails condition only)", color=t["ink2"], fontsize=9.5, va="center")
    fig.subplots_adjust(left=0.01, right=0.99, top=0.98, bottom=0.02)
    save(fig, "architecture", theme)


# ── 4. judge calibration ─────────────────────────────────────────────────────
def judge(theme):
    t = THEMES[theme]
    steps = DATA["judge_calibration"]
    fig, ax = plt.subplots(figsize=(8, 3.6), facecolor=t["surface"])
    style(ax, t)
    ax.grid(False)
    ys = range(len(steps))[::-1]
    vals = [s["agreement"] * 100 for s in steps]
    ax.barh(list(ys), vals, height=0.56, color=t["s1"], zorder=2)
    for y, s, v in zip(ys, steps, vals):
        ax.text(v - 1.5, y, f"{v:.0f}%", va="center", ha="right", color="#ffffff", fontsize=12, weight="bold", zorder=3)
        ax.text(104, y, s["note"], va="center", color=t["muted"], fontsize=10.5, clip_on=False)
    ax.set_yticks(list(ys), [s["step"] for s in steps])
    ax.set_xlim(0, 100)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_visible(False)
    fig.suptitle("Checking the AI judge against my own labels", x=0.03, y=0.95, ha="left", color=t["ink"],
                 fontsize=14, weight="bold")
    fig.text(0.03, 0.83, "Agreement on 40 blind-labeled calls. Disagreements were judge errors, my errors, or ambiguous rules.",
             color=t["muted"], fontsize=10.5)
    fig.subplots_adjust(left=0.36, right=0.69, top=0.74, bottom=0.06)
    save(fig, "judge", theme)


# ── 5. hero GIF: the side-by-side moment ─────────────────────────────────────
def hero_gif():
    spec = importlib.util.spec_from_file_location("mv", Path(__file__).parent / "make_video.py")
    mv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mv)
    from PIL import Image, ImageDraw

    run = ROOT / "runs" / "bench-20261005-125956" / "servicemember-deployment"
    if not run.exists():
        print("skip hero.gif: recorded run not found locally")
        return
    width_chars = 54
    left = mv.call_lines(run / "prompt_only-t4", width_chars)
    right = mv.call_lines(run / "guardrails-t3", width_chars)

    def window(lines):  # start just before identity verification, end after the transfer
        start = next(i for i, l in enumerate(lines) if "verify_identity" in l[0]) - 1
        return lines[start:start + 9]

    left, right = window(left), window(right)
    W, H = 1600, 430
    frames, durations = [], []
    total = max(len(left), len(right))
    for k in range(2, total + 1):  # open on content, not empty panels
        img = Image.new("RGB", (W, H), mv.BG)
        d = ImageDraw.Draw(img)
        mv.panel(d, (16, 16, W // 2 - 8, H - 16), "RULES IN PROMPT ONLY", mv.WARN, left, k)
        mv.panel(d, (W // 2 + 8, 16, W - 16, H - 16), "GUARDRAILS IN CODE", mv.BLOCK, right, k)
        frames.append(img.resize((1000, int(1000 * H / W)), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=64))
        durations.append(4500 if k == total else 650)
    frames[0].save(IMG / "hero.gif", save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)


def main():
    IMG.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        cost_reliability(theme)
        heatmap(theme)
        architecture(theme)
        judge(theme)
    hero_gif()
    for p in sorted(IMG.iterdir()):
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
