"""Figures 4-7 of the paper.

Usage: python make_figures.py numbers-benchmark.json OUT_DIR [PREVIEW_DIR] [--extended numbers-extended.json]
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path as MPath

argv = sys.argv[1:]
ext_numbers = None
if "--extended" in argv:
    i = argv.index("--extended")
    ext_numbers = json.loads(Path(argv[i + 1]).read_text("utf-8"))
    del argv[i : i + 2]
numbers = json.loads(Path(argv[0]).read_text("utf-8"))
out = Path(argv[1])
preview = Path(argv[2]) if len(argv) > 2 else None
out.mkdir(parents=True, exist_ok=True)

COL_W, TEXT_W = 3.33, 7.0
INK, INK2, MUTED, GRID = "#222222", "#52514e", "#8f8e89", "#e4e3df"
CLEAN, VIOL, REPLAY, NONF = "#1baf7a", "#eb6834", "#2a78d6", "#c9c8c2"
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times"],
        "font.size": 7.5,
        "axes.labelsize": 7.5,
        "axes.titlesize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "axes.edgecolor": MUTED,
        "axes.linewidth": 0.6,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
    }
)

MODELS = [
    ("qwen25coder32b", "Qwen2.5-Coder-32B"),
    ("qwen3coder30b", "Qwen3-Coder-30B"),
    ("qwen38_27b", "Qwen3.8-27B"),
    ("devstral2_24b", "Devstral-Small-2-24B"),
    ("gptoss120b", "gpt-oss-120b"),
    ("seedcoder8b", "Seed-Coder-8B"),
]
CLASSES = [
    ("check_then_insert", "Check-then-\ninsert"),
    ("write_skew", "Write\nskew"),
    ("predicate", "Predicate"),
    ("multi_row", "Multi-\nrow"),
]


def save(fig, name):
    fig.savefig(out / f"{name}.pdf", bbox_inches="tight", pad_inches=0.02)
    if preview:
        preview.mkdir(parents=True, exist_ok=True)
        fig.savefig(preview / f"{name}.png", dpi=220, bbox_inches="tight", pad_inches=0.02, facecolor="white")
    plt.close(fig)


def clean_axes(ax, grid_axis=None):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if grid_axis:
        ax.grid(axis=grid_axis, color=GRID, linewidth=0.5)
        ax.set_axisbelow(True)


def prevalence():
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    prev = numbers["prevalence"]
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_W, 1.75), gridspec_kw={"wspace": 0.06})
    for ax, cond, title in zip(axes, ("plain", "hint"), ("Plain prompt", "Hint prompt")):
        for i, (m, _) in enumerate(MODELS):
            for j, (c, _) in enumerate(CLASSES):
                cell = prev[f"{m}|{cond}|{c}"]
                k, n = cell["k"], cell["n"]
                share = k / n
                ax.add_patch(Rectangle((j, i), 1, 1, facecolor=cmap(share), edgecolor="white", linewidth=1.5))
                ax.text(
                    j + 0.5, i + 0.5, f"{k}/{n}", ha="center", va="center", fontsize=7,
                    color="white" if share >= 0.55 else INK,
                )
        ax.set_xlim(0, 4)
        ax.set_ylim(6, 0)
        ax.set_xticks([j + 0.5 for j in range(4)], [lab for _, lab in CLASSES])
        ax.set_yticks([i + 0.5 for i in range(6)], [lab for _, lab in MODELS] if cond == "plain" else [])
        ax.tick_params(length=0)
        ax.set_title(title, color=INK, pad=4)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.axhline(2, color=INK, linewidth=0.8)
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 1))
    cb = fig.colorbar(sm, ax=axes, fraction=0.025, pad=0.015)
    cb.set_label("Violating share of functional programs", color=INK2)
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=2)
    save(fig, "prevalence")


STATES = [("clean", "Clean", CLEAN), ("violating", "Violating", VIOL), ("nonfunctional", "Nonfunctional", NONF)]


def band(ax, x0, x1, y0a, y0b, y1a, y1b, color):
    xm = (x0 + x1) / 2
    verts = [(x0, y0a), (xm, y0a), (xm, y1a), (x1, y1a), (x1, y1b), (xm, y1b), (xm, y0b), (x0, y0b), (x0, y0a)]
    codes = [MPath.MOVETO, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.LINETO, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.CLOSEPOLY]
    ax.add_patch(PathPatch(MPath(verts, codes), facecolor=color, alpha=0.45, edgecolor="none"))


def alluvial():
    trans = dict(numbers["transitions"])
    panels = [("loop_seq", "Sequential-test loop"), ("loop_conc", "Concurrency-test loop")]
    if ext_numbers is not None:
        trans["loop_self"] = ext_numbers["e1_transitions"]["loop_self"]
        panels.append(("loop_self", "Self-test loop"))
    fig, axes = plt.subplots(1, len(panels), figsize=(TEXT_W, 1.9), gridspec_kw={"wspace": 0.42})
    gap, w = 14, 0.07
    for ax, (mode, title) in zip(axes, panels):
        flows = {tuple(k.split("->")): v for k, v in trans[mode].items()}
        left = {s: sum(v for (a, _), v in flows.items() if a == s) for s, _, _ in STATES}
        right = {s: sum(v for (_, b), v in flows.items() if b == s) for s, _, _ in STATES}

        def stack(totals):
            pos, y = {}, 0
            for s, _, _ in STATES:
                pos[s] = [y, y + totals[s]]
                y += totals[s] + gap
            return pos

        lp, rp = stack(left), stack(right)
        lcur = {s: lp[s][0] for s in lp}
        rcur = {s: rp[s][0] for s in rp}
        for a, _, _ in STATES:
            for b, _, color in STATES:
                v = flows.get((a, b), 0)
                if not v:
                    continue
                band(ax, w, 1 - w, lcur[a], lcur[a] + v, rcur[b], rcur[b] + v, color)
                lcur[a] += v
                rcur[b] += v
        for side, pos, totals, x in (("l", lp, left, 0), ("r", rp, right, 1 - w)):
            for s, label, color in STATES:
                y0, y1 = pos[s]
                ax.add_patch(Rectangle((x, y0), w, y1 - y0, facecolor=color, edgecolor="white", linewidth=0.8))
                tx = -0.02 if side == "l" else 1.02
                ax.text(tx, (y0 + y1) / 2, f"{label}\n{totals[s]}", ha="right" if side == "l" else "left", va="center", fontsize=7, color=INK)
        total = sum(left.values()) + 2 * gap
        ax.set_xlim(-0.36, 1.36)
        ax.set_ylim(total, 0)
        ax.axis("off")
        ax.set_ylim(total, -28)
        ax.set_title(title, color=INK, pad=12)
        ax.text(w / 2, -8, "Starting answer", ha="center", va="bottom", fontsize=7, color=INK2)
        ax.text(1 - w / 2, -8, "Loop answer", ha="center", va="bottom", fontsize=7, color=INK2)
    save(fig, "loops")


def remedies():
    rem = numbers["remedies"]
    cats = numbers["retry_categories"]
    fig, (ax, bx) = plt.subplots(
        1, 2, figsize=(TEXT_W, 1.55), gridspec_kw={"width_ratios": [1.35, 1], "wspace": 0.55}
    )
    configs = [
        ("ser_restored_share", "SERIALIZABLE"),
        ("retry_restored_share", "SERIALIZABLE,\nrequest retry"),
        ("replay_restored_share", "Request replay"),
    ]
    engines = [("postgresql", "PostgreSQL", "o", REPLAY, -0.13), ("mysql", "MySQL", "s", INK, 0.13)]
    for i, (key, _) in enumerate(configs):
        for e, label, marker, color, dy in engines:
            p = rem[e][key]
            ax.errorbar(
                p["share"], i + dy, xerr=[[p["share"] - p["lower"]], [p["upper"] - p["share"]]],
                fmt=marker, color=color, markersize=4.5, elinewidth=1.0, capsize=2,
                markerfacecolor=color if e == "postgresql" else "white", markeredgewidth=1.0,
                label=label if i == 0 else None,
            )
            ax.text(p["upper"] + 0.02, i + dy, f"{p['k']}/{p['n']}", va="center", fontsize=6.5, color=INK2)
    ax.set_yticks(range(3), [lab for _, lab in configs])
    ax.set_ylim(2.55, -0.55)
    ax.set_xlim(0, 1.18)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xlabel("Share restored (95% Wilson interval)", color=INK2)
    ax.tick_params(axis="y", length=0)
    clean_axes(ax, "x")
    ax.legend(loc="lower right", bbox_to_anchor=(1.02, 0.98), ncol=2, frameon=False, handletextpad=0.3, borderaxespad=0.0, columnspacing=0.8)
    ax.set_title("(a) Programs violating at the default", color=INK, fontsize=7.5, loc="left", pad=12)

    parts = [("restored", "Restored", CLEAN), ("falls_short", "Hides the failure", VIOL), ("still_violates", "Still violates", MUTED), ("raises", "Raises (1 and 2)", INK)]
    for i, (e, label, *_rest) in enumerate(engines):
        left = 0
        n = sum(cats[e].values())
        for key, plabel, color in parts:
            v = cats[e].get(key, 0)
            bx.barh(i, v / n, left=left, color=color, height=0.55, edgecolor="white", linewidth=1.0, label=plabel if i == 0 else None)
            if v / n > 0.08:
                bx.text(left + v / n / 2, i, str(v), ha="center", va="center", fontsize=6.5, color="white")
            left += v / n
    bx.set_yticks([0, 1], [lab for _, lab, *_ in engines])
    bx.set_ylim(1.5, -0.5)
    bx.set_xlim(0, 1)
    bx.set_xticks([0, 0.5, 1.0])
    bx.set_xlabel("Share under request retry", color=INK2)
    bx.tick_params(axis="y", length=0)
    clean_axes(bx)
    bx.legend(loc="upper center", bbox_to_anchor=(0.42, -0.36), ncol=4, frameon=False, handlelength=0.9, columnspacing=0.8, handletextpad=0.3)
    bx.set_title("(b) Outcome under request retry", color=INK, fontsize=7.5, loc="left", pad=12)
    save(fig, "remedies")


def remedies_column():
    rem, apps = numbers["remedies"], ext_numbers["e2_remedies"]
    rows = []
    for e, el in (("postgresql", "PostgreSQL"), ("mysql", "MySQL")):
        r = rem[e]
        n = r["replay_restored_share"]["n"]
        rows.append(("Benchmark", el, n, {
            "retry": (r["retry_restored"], r["retry_still"], n - r["retry_restored"] - r["retry_still"]),
            "replay": (r["replay_restored"], r["replay_still"], n - r["replay_restored"] - r["replay_still"])}))
    for app, al in (("oscar", "django-oscar"), ("ctfd", "CTFd")):
        for e, el in (("postgresql", "PostgreSQL"), ("mysql", "MySQL")):
            a = apps[e][app]
            n = a["violating"]
            rows.append((al, el, n, {
                "retry": (a["retry_restored"], a["retry_still_violating"], n - a["retry_restored"] - a["retry_still_violating"]),
                "replay": (a["replay_restored"], 0, n - a["replay_restored"])}))
    parts = [("Restored", CLEAN), ("Still violates", VIOL), ("Invariant kept, not restored", INK2)]
    ys, y = [], 0.0
    for i, row in enumerate(rows):
        if i and row[0] != rows[i - 1][0]:
            y += 0.45
        ys.append(y)
        y += 1.0
    fig, axes = plt.subplots(1, 2, figsize=(COL_W, 1.9), sharey=True, gridspec_kw={"wspace": 0.16})
    for ax, (key, title) in zip(axes, (("retry", "SERIALIZABLE, request retry"), ("replay", "Request replay"))):
        for yy, (_, _, n, vals) in zip(ys, rows):
            left = 0.0
            for v, (label, color) in zip(vals[key], parts):
                if not v:
                    continue
                ax.barh(yy, v / n, left=left, height=0.72, color=color, edgecolor="white", linewidth=0.8)
                if v / n >= 0.1:
                    ax.text(left + v / n / 2, yy, str(v), ha="center", va="center", fontsize=6.2, color="white")
                left += v / n
        ax.set_xlim(0, 1)
        ax.set_xticks([0, 0.5, 1.0], ["0", "0.5", "1"])
        ax.set_title(title, color=INK, fontsize=7.2, pad=3)
        ax.tick_params(axis="y", length=0)
        clean_axes(ax)
    axes[0].set_yticks(ys, [f"{eng}  ({n})" for _, eng, n, _ in rows], fontsize=6.4)
    axes[0].set_ylim(ys[-1] + 0.6, -0.6)
    for g in dict.fromkeys(r[0] for r in rows):
        gy = [yy for yy, r in zip(ys, rows) if r[0] == g]
        axes[0].text(-0.82, sum(gy) / len(gy), g, transform=axes[0].get_yaxis_transform(), ha="right", va="center",
                     fontsize=6.6, color=INK, fontweight="bold")
    fig.supxlabel("Share of the programs that violate at the default", fontsize=7, color=INK2, y=-0.06)
    handles = [Rectangle((0, 0), 1, 1, color=c) for _, c in parts]
    fig.legend(handles, [l for l, _ in parts], loc="upper center", bbox_to_anchor=(0.45, -0.10), ncol=3,
               frameon=False, handlelength=0.9, columnspacing=0.9, handletextpad=0.3, fontsize=6.4)
    save(fig, "remedies")


def contention():
    sw = numbers["sweep"]
    levels = [("w2", 2), ("w4", 4), ("w8", 8), ("w16", 16)]
    series = [
        ("reference", "replay", "Correct reference, replay", MUTED, "--", "^"),
        ("loop_repaired", "default", "Loop-repaired code, default level", CLEAN, "-", "s"),
        ("replay_restored", "replay", "Unchanged generated code, replay", REPLAY, "-", "o"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_W, 1.6), sharey=True, gridspec_kw={"wspace": 0.08})
    xs = [x for _, x in levels]
    spread_x = 30
    for ax, (e, title) in zip(axes, (("postgresql", "PostgreSQL"), ("mysql", "MySQL"))):
        ax.axhline(1.0, color=MUTED, linewidth=0.6, zorder=0)
        for pop, arm, label, color, ls, marker in series:
            cells = [sw[f"{e}|{lv}|{pop}|{arm}"] for lv, _ in levels]
            med = [c["ratio_median"] for c in cells]
            ax.fill_between(xs, [c["ratio_q1"] for c in cells], [c["ratio_q3"] for c in cells], color=color, alpha=0.13, linewidth=0)
            ax.plot(xs, med, ls, color=color, marker=marker, markersize=3.8, linewidth=1.4, label=label)
            sp = sw[f"{e}|w8-spread50ms|{pop}|{arm}"]
            ax.errorbar(spread_x * {"o": 1.08, "s": 1.0, "^": 0.92}[marker], sp["ratio_median"], yerr=[[sp["ratio_median"] - sp["ratio_q1"]], [sp["ratio_q3"] - sp["ratio_median"]]], fmt=marker, color=color, markersize=3.8, elinewidth=0.9, capsize=1.5)
        ax.axvline(22, color=GRID, linewidth=0.8)
        ax.set_xscale("log", base=2)
        ax.set_xticks(xs + [spread_x], ["2", "4", "8", "16", "8, spread"])
        ax.minorticks_off()
        ax.set_xlim(1.7, 38)
        ax.set_xlabel("Clients released together", color=INK2)
        ax.set_title(title, color=INK, pad=3)
        clean_axes(ax, "y")
    axes[0].set_ylabel("Median trial time /\nreference at default", color=INK2)
    axes[0].set_ylim(0.6, 3.6)
    axes[1].legend(loc="upper right", frameon=False, handlelength=2.2)
    save(fig, "contention")


prevalence()
alluvial()
if ext_numbers is not None:
    remedies_column()
else:
    remedies()
contention()
print("figures written to", out)
