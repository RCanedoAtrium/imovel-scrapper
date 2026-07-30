from __future__ import annotations

import os
import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from ..config import Settings
from ..errors import SchemaError, SourceError
from ..models import SourceArtifact, StageResult
from ..normalization import normalize_column_name
from ..storage import atomic_json, sha256_file


MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

IPTU_REQUIRED = {
    "AREA_CONSTRUCAO", "TIPO_CONSTRUTIVO", "PADRAO_ACABAMENTO",
    "NOME_LOGRADOURO", "NUMERO_IMOVEL", "CEP",
}
ITBI_REQUIRED = {
    "AREA CONSTRUIDA ADQUIRIDA", "VALOR DECLARADO", "VALOR BASE CALCULO",
    "BAIRRO", "DESCRICAO TIPO OCUPACAO (UNIDADE)",
}


class CKANClient:
    def __init__(self, api_base_url: str, timeout: int = 60):
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "bh-property-scraper/2.0"})

    def package_show(self, dataset: str) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.api_base_url}/package_show",
                params={"id": dataset},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            try:
                payload = self._package_show_via_browser(dataset)
            except Exception as browser_exc:
                raise SourceError(
                    f"Could not query the CKAN catalog for {dataset}: HTTP={exc}; browser={browser_exc}"
                ) from browser_exc
        if not payload.get("success") or not isinstance(payload.get("result"), dict):
            raise SourceError(f"Invalid CKAN response for dataset {dataset}")
        return payload["result"]

    def download(self, url: str, destination: Path, expected_size: int = 0) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            with self.session.get(url, stream=True, timeout=(30, 300)) as response:
                response.raise_for_status()
                with temporary.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)
            actual_size = temporary.stat().st_size
            if actual_size == 0:
                raise SourceError(f"Empty download: {url}")
            if expected_size and actual_size != expected_size:
                raise SourceError(
                    f"Unexpected size for {url}: expected {expected_size}, received {actual_size}"
                )
            os.replace(temporary, destination)
        except requests.HTTPError as exc:
            temporary.unlink(missing_ok=True)
            if exc.response is not None and exc.response.status_code == 403:
                self._download_via_browser(url, destination)
                actual_size = destination.stat().st_size
                if expected_size and actual_size != expected_size:
                    destination.unlink(missing_ok=True)
                    raise SourceError(
                        f"Unexpected size for {url}: expected {expected_size}, received {actual_size}"
                    )
                return
            raise
        except (requests.RequestException, OSError):
            temporary.unlink(missing_ok=True)
            raise
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _package_show_via_browser(self, dataset: str) -> dict[str, Any]:
        from playwright.sync_api import sync_playwright

        portal = "https://dados.pbh.gov.br"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(ignore_https_errors=True)
            try:
                page.goto(f"{portal}/dataset/{dataset}", wait_until="domcontentloaded", timeout=60_000)
                payload = page.evaluate(
                    """async (dataset) => {
                        const response = await fetch(`/api/3/action/package_show?id=${encodeURIComponent(dataset)}`);
                        if (!response.ok) throw new Error(`HTTP ${response.status}`);
                        return await response.json();
                    }""",
                    dataset,
                )
                return payload
            finally:
                browser.close()

    def _download_via_browser(self, url: str, destination: Path) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(ignore_https_errors=True)
            try:
                with page.expect_download(timeout=300_000) as download_info:
                    try:
                        page.goto(url, timeout=300_000)
                    except Exception as exc:
                        if "Download is starting" not in str(exc):
                            raise
                download_info.value.save_as(destination)
            finally:
                browser.close()


def _resource_format(resource: dict[str, Any]) -> str:
    return str(resource.get("format") or "").strip().upper()


def _resource_url(resource: dict[str, Any]) -> str:
    return str(resource.get("url") or "").strip()


def _resource_name(resource: dict[str, Any]) -> str:
    return str(resource.get("name") or resource.get("title") or "").strip()


def parse_iptu_competence(resource: dict[str, Any]) -> str | None:
    text = f"{_resource_name(resource)} {_resource_url(resource)}"
    match = re.search(r"(20\d{2})(0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[_-]regional[_-]centro[_-]sul", text, re.I)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def parse_itbi_competence(resource: dict[str, Any]) -> str | None:
    text = _resource_name(resource)
    filename = _resource_url(resource)
    machine = re.search(r"pda[_-]itbi[_-]relatorio[_-](20\d{2})(0[1-9]|1[0-2])", filename, re.I)
    if machine:
        return f"{machine.group(1)}-{machine.group(2)}"
    human = re.search(r"(20\d{2})\s*-\s*([A-Za-zÀ-ÿ]+)\s*-\s*ITBI", text, re.I)
    if not human:
        return None
    month_name = human.group(2).lower()
    month = MONTHS.get(month_name)
    return f"{human.group(1)}-{month:02d}" if month else None


def _resource_size(resource: dict[str, Any]) -> int:
    try:
        return int(resource.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def select_iptu_resource(resources: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    candidates = [
        (competence, resource)
        for resource in resources
        if _resource_format(resource) == "CSV" and (competence := parse_iptu_competence(resource))
    ]
    if not candidates:
        raise SourceError("No monthly Centro-Sul IPTU CSV resource was found")
    return max(candidates, key=lambda item: item[0])


def select_itbi_resources(resources: list[dict[str, Any]], start: str) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for resource in resources:
        if _resource_format(resource) != "CSV":
            continue
        competence = parse_itbi_competence(resource)
        if not competence or competence < start:
            continue
        current = selected.get(competence)
        if current is None or str(resource.get("last_modified") or resource.get("created") or "") > str(
            current.get("last_modified") or current.get("created") or ""
        ):
            selected[competence] = resource
    if not selected:
        raise SourceError(f"No monthly ITBI resource was found from {start}")
    return selected


def validate_csv_schema(path: Path, required: set[str]) -> list[str]:
    errors: list[Exception] = []
    first_line = path.open("r", encoding="utf-8-sig", errors="replace").readline()
    separator = ";" if first_line.count(";") > first_line.count(",") else ","
    for encoding in ("utf-8-sig", "latin1"):
        try:
            header = pd.read_csv(path, sep=separator, encoding=encoding, nrows=0)
            columns = [normalize_column_name(column) for column in header.columns]
            signatures = {re.sub(r"[^A-Z0-9]", "", column) for column in columns}
            missing = sorted(
                column for column in required
                if re.sub(r"[^A-Z0-9]", "", column) not in signatures
            )
            if missing:
                raise SchemaError(f"Missing columns in {path.name}: {', '.join(missing)}")
            return columns
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            errors.append(exc)
    raise SchemaError(f"Could not read the header of {path}: {errors}")


class PBHSynchronizer:
    def __init__(self, settings: Settings, client: CKANClient | None = None):
        self.settings = settings
        self.client = client or CKANClient(settings.api_base_url)

    def sync(self, force: bool = False) -> StageResult:
        artifacts: list[SourceArtifact] = []
        warnings: list[str] = []

        iptu_package = self.client.package_show(self.settings.iptu_dataset)
        competence, iptu_resource = select_iptu_resource(iptu_package.get("resources", []))
        iptu_artifact = self._sync_resource(
            source="iptu",
            competence=competence,
            resource=iptu_resource,
            directory=self.settings.paths.iptu,
            required=IPTU_REQUIRED,
            force=force,
        )
        artifacts.append(iptu_artifact)
        removed_iptu_snapshots = self._prune_iptu_snapshots(Path(iptu_artifact.path))
        warnings.extend(self._iptu_staleness_warning(competence))

        itbi_package = self.client.package_show(self.settings.itbi_dataset)
        itbi_resources = select_itbi_resources(
            itbi_package.get("resources", []), self.settings.itbi_history_start
        )
        for itbi_competence, resource in sorted(itbi_resources.items()):
            artifacts.append(
                self._sync_resource(
                    source="itbi",
                    competence=itbi_competence,
                    resource=resource,
                    directory=self.settings.paths.itbi,
                    required=ITBI_REQUIRED,
                    force=force,
                )
            )

        catalog_path = self.settings.paths.data / "pbh_catalog.json"
        atomic_json(
            catalog_path,
            {
                "synced_at": datetime.now(timezone.utc).isoformat(),
                "artifacts": [asdict(item) for item in artifacts],
                "removed_iptu_snapshots": removed_iptu_snapshots,
                "warnings": warnings,
            },
        )
        return StageResult(
            stage="sync-pbh",
            status="warning" if warnings else "success",
            artifacts=[item.path for item in artifacts] + [str(catalog_path)],
            counts={
                "iptu_resources": 1,
                "itbi_resources": len(itbi_resources),
                "downloaded": sum(item.status == "downloaded" for item in artifacts),
                "reused": sum(item.status == "reused" for item in artifacts),
                "removed_iptu_snapshots": len(removed_iptu_snapshots),
            },
            warnings=warnings,
            details={"latest_iptu": competence, "latest_itbi": max(itbi_resources)},
        )

    def _prune_iptu_snapshots(self, current: Path) -> list[str]:
        current = current.resolve()
        current_metadata = current.with_suffix(current.suffix + ".metadata.json")
        removed: list[str] = []
        for path in self.settings.paths.iptu.iterdir():
            if not path.is_file() or path.resolve() in {current, current_metadata.resolve()}:
                continue
            if path.suffix.lower() == ".csv" or path.name.endswith(".csv.metadata.json"):
                path.unlink()
                removed.append(str(path))
        return removed

    def _sync_resource(
        self,
        source: str,
        competence: str,
        resource: dict[str, Any],
        directory: Path,
        required: set[str],
        force: bool,
    ) -> SourceArtifact:
        url = _resource_url(resource)
        if not url:
            raise SourceError(f"Resource {resource.get('id')} has no download URL")
        filename = Path(url.split("?", 1)[0]).name or f"{source}_{competence.replace('-', '')}.csv"
        destination = directory / filename
        remote_hash = str(resource.get("hash") or "").strip().lower()
        metadata_path = destination.with_suffix(destination.suffix + ".metadata.json")
        should_download = force or not destination.exists()
        if destination.exists() and remote_hash:
            local_metadata: dict[str, Any] = {}
            if metadata_path.exists():
                try:
                    import json
                    local_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    local_metadata = {}
            if local_metadata.get("remote_hash") != remote_hash:
                should_download = True

        if should_download:
            self.client.download(url, destination, _resource_size(resource))
        columns = validate_csv_schema(destination, required)
        digest = sha256_file(destination)
        metadata = {
            "source": source,
            "resource_id": str(resource.get("id") or ""),
            "url": url,
            "competence": competence,
            "published_at": str(resource.get("last_modified") or resource.get("created") or ""),
            "size": destination.stat().st_size,
            "sha256": digest,
            "remote_hash": remote_hash,
            "columns": columns,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_json(metadata_path, metadata)
        return SourceArtifact(
            source=source,
            path=str(destination),
            resource_id=metadata["resource_id"],
            url=url,
            competence=competence,
            published_at=metadata["published_at"],
            size=metadata["size"],
            sha256=digest,
            status="downloaded" if should_download else "reused",
        )

    def _iptu_staleness_warning(self, competence: str) -> list[str]:
        year, month = (int(part) for part in competence.split("-"))
        snapshot = date(year, month, 1)
        age = (date.today() - snapshot).days
        if age > self.settings.iptu_staleness_days:
            return [
                f"The latest IPTU snapshot ({competence}) is approximately {age} days old"
            ]
        return []
