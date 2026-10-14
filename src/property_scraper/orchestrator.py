from __future__ import annotations

import uuid
from dataclasses import asdict

from .alerts import generate_alerts, save_alerts
from .analysis import analyze
from .config import Settings
from .errors import DataQualityError
from .models import RunManifest, StageResult
from .processing import prepare_all
from .reporting import generate_report
from .sources.listings import ListingCollector
from .sources.pbh import PBHSynchronizer
from .storage import atomic_json, read_dataset


def collect_reference_data(settings: Settings, *, force: bool = False) -> StageResult:
    return PBHSynchronizer(settings).sync(force=force)


def scrape_listings(settings: Settings, *, run_id: str) -> StageResult:
    return ListingCollector(settings).collect(run_id=run_id)


def treat_local_data(settings: Settings) -> StageResult:
    return prepare_all(settings)


def infer_patterns(settings: Settings) -> StageResult:
    return analyze(settings)


def generate_pipeline_alerts(settings: Settings) -> StageResult:
    analysis_path = settings.paths.processed / "analysis.csv"
    if not analysis_path.exists():
        raise DataQualityError("Run analyze before generating alerts")
    analysis = read_dataset(analysis_path)
    alerts = generate_alerts(analysis, settings)
    json_path, md_path = save_alerts(alerts, settings)
    return StageResult(
        stage="alerts",
        artifacts=[str(json_path), str(md_path)],
        counts={
            "new_opportunities": alerts["new_opportunities_count"],
            "price_drops": alerts["price_drops_count"],
        },
        details={"markdown_digest": alerts["markdown_digest"]},
    )


def run_pipeline(
    settings: Settings,
    force_sync: bool = False,
    *,
    sync_reference: bool = True,
    scrape: bool = True,
    report: bool = False,
    alert: bool = False,
) -> StageResult:
    run_id = uuid.uuid4().hex[:12]
    manifest = RunManifest(
        run_id=run_id,
        command="run",
        parameters={
            "sites": settings.sites,
            "neighborhoods": settings.neighborhoods,
            "max_price": settings.max_price,
            "max_pages_per_neighborhood": settings.pages_per_neighborhood,
            "min_pages_per_neighborhood": settings.min_pages_per_neighborhood,
            "min_new_listings_per_page": settings.min_new_listings_per_page,
            "discount_threshold": settings.discount_threshold,
            "classifier": settings.classifier,
            "sync_reference": sync_reference,
            "scrape": scrape,
            "report": report,
            "alert": alert,
        },
    )
    manifest_path = settings.paths.reports / f"run_{run_id}.json"
    try:
        stages: list[StageResult] = []
        if sync_reference:
            stages.append(collect_reference_data(settings, force=force_sync))
        if scrape:
            stages.append(scrape_listings(settings, run_id=run_id))
        stages.append(treat_local_data(settings))
        stages.append(infer_patterns(settings))
        if report:
            stages.append(generate_report(settings, run_id=run_id))
        if alert:
            stages.append(generate_pipeline_alerts(settings))
        manifest.stages = [stage.to_dict() for stage in stages]
        manifest.finish("success")
        atomic_json(manifest_path, asdict(manifest))
        for previous in settings.paths.reports.glob("run_*.json"):
            if previous != manifest_path:
                previous.unlink()
        return StageResult(
            stage="run",
            artifacts=[artifact for stage in stages for artifact in stage.artifacts] + [str(manifest_path)],
            counts={stage.stage: sum(stage.counts.values()) for stage in stages},
            details={"run_id": run_id, "stages": manifest.stages},
        )
    except Exception as exc:
        manifest.finish("failed")
        manifest.stages.append({"stage": "failed", "error": str(exc)})
        atomic_json(manifest_path, asdict(manifest))
        raise
