from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Settings
from .storage import atomic_json


def generate_alerts(
    analysis: pd.DataFrame,
    settings: Settings,
    min_price_drop_pct: float | None = None,
) -> dict[str, Any]:
    """Generates structured notification alerts for new high-confidence opportunities and price drops."""
    if analysis.empty:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "new_opportunities_count": 0,
            "price_drops_count": 0,
            "new_opportunities": [],
            "price_drops": [],
            "markdown_digest": "# 🏠 BH Property Opportunities\n\nNo active listings found.",
        }

    drop_threshold = min_price_drop_pct if min_price_drop_pct is not None else settings.rules.min_price_drop_pct

    # 1. New qualified opportunities (eligible or pending review, first observation)
    new_opp_mask = (
        analysis["ranking_status"].isin(["parking_eligible", "parking_pending_review"])
        & (
            analysis.get("observations_count", pd.Series(1, index=analysis.index)).le(1)
            | (analysis.get("first_seen", "") == analysis.get("collected_date", ""))
        )
    )
    new_opps_df = analysis[new_opp_mask].sort_values(
        by=["ranking_priority", "opportunity_score", "discount_vs_conservative_ref"],
        ascending=[False, False, False],
    )

    # 2. Price drops meeting threshold
    price_drop_mask = (
        analysis.get("price_drop", pd.Series(0.0, index=analysis.index)).gt(0)
        & analysis.get("price_drop_pct", pd.Series(0.0, index=analysis.index)).ge(drop_threshold)
    )
    price_drops_df = analysis[price_drop_mask].sort_values(
        by=["price_drop_pct", "discount_vs_conservative_ref"],
        ascending=[False, False],
    )

    def row_to_dict(row: pd.Series) -> dict[str, Any]:
        return {
            "title": str(row.get("title", "")),
            "neighborhood": str(row.get("neighborhood", "")),
            "price": float(row.get("price", 0) or 0),
            "min_advertised_price": float(row.get("min_advertised_price", 0) or 0),
            "price_drop": float(row.get("price_drop", 0) or 0),
            "price_drop_pct": float(row.get("price_drop_pct", 0) or 0),
            "discount_vs_conservative_ref": float(row.get("discount_vs_conservative_ref", 0) or 0),
            "opportunity_score": float(row.get("opportunity_score", 0) or 0),
            "score_breakdown": str(row.get("score_breakdown", "")),
            "area_m2": float(row.get("area_m2", 0) or 0),
            "price_m2": float(row.get("price_m2", 0) or 0),
            "parking_spaces": float(row.get("parking_spaces", 0) or 0),
            "parking_type": str(row.get("parking_type", "")),
            "ranking_status": str(row.get("ranking_status", "")),
            "url": str(row.get("url", "")),
            "source": str(row.get("source", "")),
            "days_on_market": int(row.get("days_on_market", 0) or 0),
        }

    new_opps = [row_to_dict(row) for _, row in new_opps_df.iterrows()]
    price_drops = [row_to_dict(row) for _, row in price_drops_df.iterrows()]

    # Format Markdown digest
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    md_lines = [
        f"# 🏠 Belo Horizonte Real Estate Alert - {date_str}",
        "",
        f"**New Qualified Opportunities:** {len(new_opps)} | **Price Drops:** {len(price_drops)}",
        "",
    ]

    if new_opps:
        md_lines.append("## ✨ New Opportunities")
        for item in new_opps[:15]:
            score_txt = f"Score: {item['opportunity_score']:.1f}/100"
            discount_pct = item["discount_vs_conservative_ref"] * 100
            price_fmt = f"R$ {item['price']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            url_link = f"[{item['title']}]({item['url']})" if item["url"] else item["title"]
            md_lines.append(
                f"- **{url_link}**\n"
                f"  - **Bairro:** {item['neighborhood']} | **Preço:** {price_fmt} | **Desconto:** {discount_pct:.1f}% ({score_txt})\n"
                f"  - **Área:** {item['area_m2']:.0f} m² | **Vagas:** {item['parking_spaces']:.0f} ({item['parking_type'] or 'N/I'}) | **Status:** {item['ranking_status']}"
            )
        md_lines.append("")

    if price_drops:
        md_lines.append("## 📉 Price Drops")
        for item in price_drops[:15]:
            drop_pct = item["price_drop_pct"] * 100
            drop_val = f"R$ {item['price_drop']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            price_fmt = f"R$ {item['price']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            url_link = f"[{item['title']}]({item['url']})" if item["url"] else item["title"]
            md_lines.append(
                f"- **{url_link}**\n"
                f"  - **Bairro:** {item['neighborhood']} | **Preço:** {price_fmt} | **Queda:** -{drop_pct:.1f}% (-{drop_val})\n"
                f"  - **Desconto Cons.:** {item['discount_vs_conservative_ref']*100:.1f}% | **Dias no mercado:** {item['days_on_market']} dias"
            )
        md_lines.append("")

    if not new_opps and not price_drops:
        md_lines.append("No new opportunities or price drops identified in this run.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "new_opportunities_count": len(new_opps),
        "price_drops_count": len(price_drops),
        "new_opportunities": new_opps,
        "price_drops": price_drops,
        "markdown_digest": "\n".join(md_lines),
    }


def save_alerts(alerts: dict[str, Any], settings: Settings) -> tuple[Path, Path]:
    """Persists alert digest to JSON and latest Markdown report."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = settings.paths.reports / f"alerts_{timestamp}.json"
    latest_md_path = settings.paths.reports / "latest_alerts.md"

    atomic_json(json_path, alerts)
    latest_md_path.write_text(alerts.get("markdown_digest", ""), encoding="utf-8")
    return json_path, latest_md_path
