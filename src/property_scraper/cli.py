from __future__ import annotations

import argparse
import json
import sys
import uuid

from .analysis import analyze
from .card_validation import validate_cards
from .config import load_settings
from .errors import PipelineError
from .orchestrator import generate_pipeline_alerts, run_pipeline
from .processing import prepare_all
from .reporting import generate_report
from .sources.listings import ListingCollector
from .sources.pbh import PBHSynchronizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Modular pipeline for Belo Horizonte real-estate opportunities")
    parser.add_argument("--config", default="config.json", help="Path to the JSON configuration file")
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync-pbh", help="Synchronize official PBH CSV files")
    sync.add_argument("--force", action="store_true", help="Download remote resources again")

    scrape = commands.add_parser("scrape", help="Collect listings from real-estate portals")
    scrape.add_argument("--sites", help="Comma-separated portal names")
    scrape.add_argument("--pages", type=int, help="Pages per neighborhood")
    scrape.add_argument("--max-price", type=float, help="Global price cap applied to every neighborhood")
    scrape.add_argument("--neighborhoods", help="Comma-separated neighborhoods for an incremental collection")
    scrape.add_argument(
        "--append-latest",
        action="store_true",
        help="Merge collection into the latest run without repeating its neighborhoods",
    )
    scrape.add_argument("--retry-failed", action="store_true", help="Retry failed pages from the latest run")

    validate = commands.add_parser(
        "validate-cards", help="Validate extracted card fields without changing production data"
    )
    validate.add_argument("--neighborhood", default="BELVEDERE")
    validate.add_argument("--max-price", type=float, default=800000)
    validate.add_argument("--pages", type=int, default=4)
    validate.add_argument("--attempts", type=int, default=3)
    validate.add_argument("--sites", help="Comma-separated portal names")

    commands.add_parser("prepare", help="Normalize local sources and write CSV datasets")

    analysis = commands.add_parser("analyze", help="Match, classify, and identify opportunities")
    analysis.add_argument("--classifier", choices=["ml", "heuristics"])
    analysis.add_argument("--discount", type=float)

    report = commands.add_parser("report", help="Generate the Excel report")
    report.add_argument("--run-id", default="manual")

    alerts_parser = commands.add_parser(
        "alerts", help="Generate notification alerts for new opportunities and price drops"
    )
    alerts_parser.add_argument("--markdown", action="store_true", help="Print only markdown digest to stdout")

    run = commands.add_parser("run", help="Run every pipeline stage")
    run.add_argument("--force-sync", action="store_true")
    run.add_argument(
        "--skip-reference-sync",
        action="store_true",
        help="Use local ITBI/IPTU CSV files without querying PBH",
    )
    run.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Use the latest local listing run without accessing the portals",
    )
    run.add_argument("--report", action="store_true", help="Also generate the Excel report")
    run.add_argument("--alert", action="store_true", help="Also generate notification alerts")
    return parser


def execute(args: argparse.Namespace):
    settings = load_settings(args.config)
    if args.command == "sync-pbh":
        return PBHSynchronizer(settings).sync(force=args.force)
    if args.command == "scrape":
        sites = [site.strip() for site in args.sites.split(",")] if args.sites else None
        neighborhoods = [name.strip() for name in args.neighborhoods.split(",")] if args.neighborhoods else None
        if args.max_price is not None:
            if args.max_price <= 0:
                raise PipelineError("The global price cap must be greater than zero")
        base_run_id = None
        if args.append_latest:
            latest = settings.paths.listings / "latest.txt"
            if not latest.exists():
                raise PipelineError("There is no previous run to merge")
            base_run_id = latest.read_text(encoding="utf-8").strip()
        return ListingCollector(settings).collect(
            sites=sites,
            pages_per_neighborhood=args.pages,
            run_id=uuid.uuid4().hex[:12],
            max_price=args.max_price,
            neighborhoods=neighborhoods,
            base_run_id=base_run_id,
            retry_failed=args.retry_failed,
        )
    if args.command == "validate-cards":
        sites = [site.strip() for site in args.sites.split(",")] if args.sites else None
        return validate_cards(
            settings,
            neighborhood=args.neighborhood,
            max_price=args.max_price,
            pages=args.pages,
            attempts=args.attempts,
            sites=sites,
        )
    if args.command == "prepare":
        return prepare_all(settings)
    if args.command == "analyze":
        return analyze(settings, classifier_mode=args.classifier, discount_threshold=args.discount)
    if args.command == "report":
        return generate_report(settings, run_id=args.run_id)
    if args.command == "alerts":
        res = generate_pipeline_alerts(settings)
        if getattr(args, "markdown", False):
            print(res.details.get("markdown_digest", ""))
        return res
    if args.command == "run":
        return run_pipeline(
            settings,
            force_sync=args.force_sync,
            sync_reference=not args.skip_reference_sync,
            scrape=not args.skip_scrape,
            report=args.report,
            alert=args.alert,
        )
    parser_error = f"Unknown command: {args.command}"
    raise PipelineError(parser_error)


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    except PipelineError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
