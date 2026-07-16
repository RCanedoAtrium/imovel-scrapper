from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict

from .analysis import analyze
from .card_validation import validate_cards
from .config import load_settings
from .errors import PipelineError
from .orchestrator import run_pipeline
from .processing import prepare_all
from .reporting import generate_report
from .sources.listings import ListingCollector
from .sources.pbh import PBHSynchronizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pipeline modular de oportunidades imobiliarias em BH")
    parser.add_argument("--config", default="config.json", help="Arquivo JSON de configuracao")
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync-pbh", help="Sincroniza os CSVs oficiais da PBH")
    sync.add_argument("--force", action="store_true", help="Baixa novamente os recursos remotos")

    scrape = commands.add_parser("scrape", help="Coleta anuncios dos portais")
    scrape.add_argument("--sites", help="Portais separados por virgula")
    scrape.add_argument("--pages", type=int, help="Paginas por bairro")
    scrape.add_argument("--max-price", type=float, help="Teto global aplicado a todos os bairros")
    scrape.add_argument("--neighborhoods", help="Bairros separados por virgula para coleta incremental")
    scrape.add_argument("--append-latest", action="store_true", help="Mescla a coleta ao último run, sem repetir seus bairros")

    validate = commands.add_parser("validate-cards", help="Valida campos extraidos dos cards sem alterar dados de producao")
    validate.add_argument("--neighborhood", default="BELVEDERE")
    validate.add_argument("--max-price", type=float, default=800000)
    validate.add_argument("--pages", type=int, default=4)
    validate.add_argument("--attempts", type=int, default=3)
    validate.add_argument("--sites", help="Portais separados por virgula")

    commands.add_parser("prepare", help="Normaliza fontes locais e grava datasets CSV")

    analysis = commands.add_parser("analyze", help="Cruza, classifica e identifica oportunidades")
    analysis.add_argument("--classifier", choices=["ml", "heuristics"])
    analysis.add_argument("--discount", type=float)

    report = commands.add_parser("report", help="Gera o relatorio Excel")
    report.add_argument("--run-id", default="manual")

    run = commands.add_parser("run", help="Executa todas as etapas")
    run.add_argument("--force-sync", action="store_true")
    run.add_argument(
        "--skip-reference-sync",
        action="store_true",
        help="Usa os CSVs ITBI/IPTU locais sem consultar a PBH",
    )
    run.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Usa o ultimo run local de anuncios sem acessar os portais",
    )
    run.add_argument("--report", action="store_true", help="Gera tambem o relatorio Excel")
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
                raise PipelineError("O teto global deve ser maior que zero")
        base_run_id = None
        if args.append_latest:
            latest = settings.paths.listings / "latest.txt"
            if not latest.exists():
                raise PipelineError("Não existe run anterior para mesclar")
            base_run_id = latest.read_text(encoding="utf-8").strip()
        return ListingCollector(settings).collect(
            sites=sites,
            pages_per_neighborhood=args.pages,
            run_id=uuid.uuid4().hex[:12],
            max_price=args.max_price,
            neighborhoods=neighborhoods,
            base_run_id=base_run_id,
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
    if args.command == "run":
        return run_pipeline(
            settings,
            force_sync=args.force_sync,
            sync_reference=not args.skip_reference_sync,
            scrape=not args.skip_scrape,
            report=args.report,
        )
    parser_error = f"Comando desconhecido: {args.command}"
    raise PipelineError(parser_error)


def main(argv: list[str] | None = None) -> None:
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
