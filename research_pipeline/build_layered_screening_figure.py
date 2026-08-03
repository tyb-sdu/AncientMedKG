from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle

from research_pipeline.layered_thresholds import load_policy, validate_policy


MM_PER_INCH = 25.4
FIGURE_WIDTH_MM = 183.0
FIGURE_HEIGHT_MM = 121.0
INK = "#202124"
MID = "#5F6368"
RULE = "#C9CDD1"
ANCIENT = "#8D3A32"
ANCIENT_LIGHT = "#F5ECEA"
MODERN = "#315A72"
MODERN_LIGHT = "#EAF1F4"
REJECT = "#8A4740"
PANEL_BG = "#FAFAF9"


def _configure_matplotlib() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Microsoft YaHei", "Arial", "DejaVu Sans"],
            "font.size": 7.2,
            "axes.unicode_minus": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "savefig.facecolor": "white",
            "figure.facecolor": "white",
        }
    )


def _box(ax, x: float, y: float, w: float, h: float, *, face: str, edge: str) -> None:
    ax.add_patch(
        Rectangle(
            (x, y),
            w,
            h,
            facecolor=face,
            edgecolor=edge,
            linewidth=0.85,
            joinstyle="miter",
            zorder=2,
        )
    )


def _arrow(ax, x0: float, x1: float, y: float, color: str) -> None:
    ax.add_patch(
        FancyArrowPatch(
            (x0, y),
            (x1, y),
            arrowstyle="-|>",
            mutation_scale=8,
            linewidth=0.9,
            color=color,
            shrinkA=0,
            shrinkB=0,
            zorder=3,
        )
    )


def _stage(
    ax,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    title: str,
    body: str,
    count: str,
    face: str,
    edge: str,
) -> None:
    _box(ax, x, y, w, h, face=face, edge=edge)
    ax.text(x + 0.06, y + h - 0.09, title, ha="left", va="top", color=edge, weight="bold", fontsize=7.1)
    ax.text(x + 0.06, y + h - 0.28, body, ha="left", va="top", color=INK, fontsize=6.0, linespacing=1.22)
    ax.text(x + 0.06, y + 0.08, count, ha="left", va="bottom", color=INK, weight="bold", fontsize=6.7)


def _rejection(ax, x: float, y: float, text: str) -> None:
    ax.plot([x, x], [y + 0.04, y + 0.18], color=REJECT, linewidth=0.75, zorder=1)
    ax.text(x + 0.02, y, text, ha="left", va="top", color=REJECT, fontsize=5.9, linespacing=1.15)


def build_figure(
    policy_path: Path,
    output_svg: Path,
    output_pdf: Path | None,
    output_png: Path | None,
    output_tiff: Path | None,
    dpi: int,
    force: bool,
) -> None:
    report = validate_policy(policy_path)
    if not report["valid"]:
        raise ValueError("threshold policy failed validation: " + "; ".join(report["issues"]))
    if output_svg.suffix.lower() != ".svg":
        raise ValueError("--output-svg must use the .svg extension")
    if output_pdf is not None and output_pdf.suffix.lower() != ".pdf":
        raise ValueError("--output-pdf must use the .pdf extension")
    if output_png is not None and output_png.suffix.lower() != ".png":
        raise ValueError("--output-png must use the .png extension")
    if output_tiff is not None and output_tiff.suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError("--output-tiff must use the .tif or .tiff extension")
    for output in (output_svg, output_pdf, output_png, output_tiff):
        if output is not None and output.exists() and not force:
            raise FileExistsError(f"refusing to overwrite {output}; use --force")

    policy = load_policy(policy_path)
    baseline = policy["observed_baseline"]
    ancient = baseline["ancient"]
    modern = baseline["modern"]

    _configure_matplotlib()
    fig, ax = plt.subplots(
        figsize=(FIGURE_WIDTH_MM / MM_PER_INCH, FIGURE_HEIGHT_MM / MM_PER_INCH),
        constrained_layout=False,
    )
    fig.subplots_adjust(left=0.025, right=0.99, bottom=0.045, top=0.93)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7.25)
    ax.axis("off")

    ax.text(0.02, 7.18, "分层阈值筛选：从来源质量到可溯源发布", ha="left", va="top", fontsize=11.2, weight="bold", color=INK)
    ax.text(0.02, 6.83, "数值阈值与硬约束逐层叠加；上一层通过不替代下一层验证", ha="left", va="top", fontsize=6.8, color=MID)

    headers = [
        ("Q1", "来源质量"),
        ("Q2", "领域相关性"),
        ("Q3", "证据准入"),
        ("Q4", "机制优先级"),
        ("Q5", "发布完整性"),
    ]
    x_positions = [0.72, 2.55, 4.38, 6.21, 8.04]
    box_w = 1.56
    for x, (code, label) in zip(x_positions, headers):
        ax.text(x, 6.42, code, ha="left", va="bottom", color=INK, fontsize=6.2, weight="bold")
        ax.text(x + 0.28, 6.42, label, ha="left", va="bottom", color=MID, fontsize=6.2)
    ax.plot([0.02, 9.98], [6.32, 6.32], color=RULE, linewidth=0.8)

    ancient_y = 3.78
    modern_y = 0.75
    box_h = 1.72
    ax.add_patch(Rectangle((0.02, ancient_y - 0.36), 9.96, 2.42, facecolor=PANEL_BG, edgecolor="none", zorder=0))
    ax.add_patch(Rectangle((0.02, modern_y - 0.36), 9.96, 2.42, facecolor=PANEL_BG, edgecolor="none", zorder=0))
    ax.text(0.10, ancient_y + box_h - 0.02, "a", ha="left", va="top", fontsize=9.0, weight="bold", color=INK)
    ax.text(0.10, ancient_y + box_h - 0.28, "古籍证据", ha="left", va="top", fontsize=7.2, weight="bold", color=ANCIENT)
    ax.text(0.10, modern_y + box_h - 0.02, "b", ha="left", va="top", fontsize=9.0, weight="bold", color=INK)
    ax.text(0.10, modern_y + box_h - 0.28, "现代证据", ha="left", va="top", fontsize=7.2, weight="bold", color=MODERN)

    ancient_stages = [
        ("OCR／定本页", "OCR ≥ 0.82\n字数 ≥ 20；汉字比 ≥ 0.45\n定本文本质量 > 0.70", f"22 部 · {ancient['corpus']['pages']:,} 页"),
        ("语义候选", "直接通道 ≥ 0.80\n或迁移通道 ≥ 0.70\n且覆盖 ≥ 2 个语义层", f"{ancient['domain_candidates']['candidate_pages']:,} 个候选页"),
        ("证据与关系", "候选置信度 ≥ 0.70\n页 ID + 双哈希齐全\n低置信 OCR 禁止发布", f"{ancient['candidate_graph']['evidence']:,} 证据 · {ancient['candidate_graph']['relations']:,} 关系"),
        ("图谱准入", "证据与关系分别过门\n排除语境优先\n不满足结构约束即剔除", f"{ancient['released_graph']['evidence']:,} 证据 · {ancient['released_graph']['relations']:,} 关系"),
        ("来源发布", "逐条原页精确回读\n稳定 ID 唯一\nFTS 一致；TREATS = 0", f"{ancient['released_graph']['source_verified_evidence']:,}/{ancient['released_graph']['evidence']:,} 来源通过"),
    ]
    modern_stages = [
        ("文献与成分", "PDF／chunk 哈希一致\n成分身份 13/13\n物理页定位必须存在", f"{modern['corpus']['documents']:,} 篇 · {modern['corpus']['chunks']:,} chunks"),
        ("语境定位", "精确成分词 + 烧伤／创面\n定位置信度 ≥ 0.70\ndoc／chunk／page 一致", f"{modern['domain_candidates']['candidate_loci']:,} 个候选定位"),
        ("结构化证据", "语义置信度 ≥ 0.70\n研究类型明确\n结局或安全字段至少一项", f"{modern['structured_release']['approved']:,} 批准 · {modern['structured_release']['discarded']:,} 丢弃"),
        ("机制优先级", "成分 Tier 1 ≥ 0.75\nTier 2：0.60–0.75\nPPI ≥ 0.70；通路 FDR < 0.05", f"{modern['released_graph']['entities']:,} 实体 · {modern['released_graph']['relations']:,} 关系"),
        ("链路发布", "成分—靶点—通路—表型\n四级链路必须完整\n证据 ID 与来源逐条可回读", f"{modern['mechanism_output']['complete_compound_target_pathway_phenotype_chains']:,} 条完整候选链"),
    ]

    for stages, y, face, edge in (
        (ancient_stages, ancient_y, ANCIENT_LIGHT, ANCIENT),
        (modern_stages, modern_y, MODERN_LIGHT, MODERN),
    ):
        for index, (title, body, count) in enumerate(stages):
            x = x_positions[index]
            _stage(ax, x, y, box_w, box_h, title=title, body=body, count=count, face=face, edge=edge)
            if index < len(stages) - 1:
                _arrow(ax, x + box_w + 0.05, x_positions[index + 1] - 0.05, y + box_h / 2, edge)

    _rejection(ax, 1.50, ancient_y - 0.18, f"定本页锚排除 {ancient['corpus']['kanripo_discarded_page_anchors']:,}")
    _rejection(ax, 5.16, ancient_y - 0.18, f"发布层剔除：{ancient['discarded_at_release']['evidence']:,} 证据／{ancient['discarded_at_release']['entities']:,} 实体／{ancient['discarded_at_release']['relations']:,} 关系")
    _rejection(ax, 3.33, modern_y - 0.18, f"未进入结构化 {modern['domain_candidates']['candidate_loci'] - modern['domain_candidates']['structuring_candidates']:,} 定位")
    _rejection(ax, 5.16, modern_y - 0.18, f"字段或语义门未通过 {modern['structured_release']['discarded']:,} 条")

    ax.text(0.02, 0.16, "阈值类型", ha="left", va="center", fontsize=6.0, color=MID, weight="bold")
    legend = [
        (ANCIENT, "来源质量 0.82／0.70"),
        (INK, "语义 0.80／0.70"),
        (MODERN, "优先级 0.75／0.60"),
        (REJECT, "统计 FDR 0.05"),
        (MID, "硬门：页码、ID、哈希、结构约束"),
    ]
    lx = 0.92
    for color, label in legend:
        ax.plot([lx, lx + 0.18], [0.16, 0.16], color=color, linewidth=2.0)
        ax.text(lx + 0.24, 0.16, label, ha="left", va="center", fontsize=5.85, color=INK)
        lx += 1.77 if label != legend[-1][1] else 0

    output_svg.parent.mkdir(parents=True, exist_ok=True)
    svg_metadata = {
        "Title": "Layered threshold screening for the AncientMedRAG evidence pipeline",
        "Creator": "AncientMedRAG",
        "Description": "Stage-specific screening gates for ancient and modern evidence.",
        "Date": None,
    }
    fig.savefig(output_svg, format="svg", metadata=svg_metadata)
    svg_text = output_svg.read_text(encoding="utf-8")
    output_svg.write_text(
        "\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if output_pdf is not None:
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            output_pdf,
            format="pdf",
            metadata={
                "Title": svg_metadata["Title"],
                "Creator": svg_metadata["Creator"],
                "Subject": svg_metadata["Description"],
                "CreationDate": None,
                "ModDate": None,
            },
        )
    if output_png is not None:
        output_png.parent.mkdir(parents=True, exist_ok=True)
        if dpi == 600:
            fig.savefig(output_png, format="png", dpi=600, metadata={"Software": "AncientMedRAG"})
        else:
            fig.savefig(output_png, format="png", dpi=dpi, metadata={"Software": "AncientMedRAG"})
    if output_tiff is not None:
        output_tiff.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_tiff, format="tiff", dpi=600, pil_kwargs={"compression": "tiff_lzw"})
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Build the layered threshold screening figure")
    parser.add_argument("--policy", type=Path, default=root / "data" / "layered_thresholds_v1.json")
    parser.add_argument("--output-svg", type=Path, required=True)
    parser.add_argument("--output-pdf", type=Path)
    parser.add_argument("--output-png", type=Path)
    parser.add_argument("--output-tiff", type=Path)
    parser.add_argument("--dpi", type=int, default=600)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.dpi < 150:
        raise ValueError("dpi must be at least 150")
    build_figure(
        args.policy,
        args.output_svg,
        args.output_pdf,
        args.output_png,
        args.output_tiff,
        args.dpi,
        args.force,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
