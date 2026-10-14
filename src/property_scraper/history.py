from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .normalization import extract_listing_id


class ListingHistoryStore:
    """Persistent SQLite store tracking property listing observations, price drops, and days on market."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS listing_history (
                    portal TEXT NOT NULL,
                    listing_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    neighborhood TEXT,
                    street TEXT,
                    number TEXT,
                    area_m2 REAL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    initial_price REAL NOT NULL,
                    previous_price REAL NOT NULL,
                    latest_price REAL NOT NULL,
                    price_drop REAL NOT NULL DEFAULT 0.0,
                    price_drop_pct REAL NOT NULL DEFAULT 0.0,
                    days_on_market INTEGER NOT NULL DEFAULT 0,
                    observations_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (portal, listing_id)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_portal_id ON listing_history(portal, listing_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_price_drop ON listing_history(price_drop_pct);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_neighborhood ON listing_history(neighborhood);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_last_seen ON listing_history(last_seen);")
            conn.commit()

    def prune_history(self, retention_days: int = 180, now: datetime | None = None) -> int:
        """Prunes listing records that have not been seen in the last `retention_days`."""
        if retention_days <= 0:
            return 0
        now_dt = now or datetime.now(timezone.utc)
        cutoff_date = (now_dt - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM listing_history WHERE last_seen < ?", [cutoff_date])
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    def record_observations(
        self,
        listings: pd.DataFrame,
        now: datetime | None = None,
        retention_days: int = 180,
    ) -> pd.DataFrame:
        """Records observations of listings in SQLite and enriches the DataFrame with tracking columns."""
        if listings.empty:
            return listings

        enriched = listings.copy()
        now_dt = now or datetime.now(timezone.utc)
        current_date_str = now_dt.strftime("%Y-%m-%d")

        # Prune records older than retention threshold (GEMINI.md §3 / P3: 180 days)
        self.prune_history(retention_days=retention_days, now=now_dt)

        if "listing_id" not in enriched.columns:
            enriched["listing_id"] = [
                extract_listing_id(str(row.get("url", "")), str(row.get("source", "")))
                for _, row in enriched.iterrows()
            ]

        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Collect candidate keys
            candidate_keys = []
            for _, row in enriched.iterrows():
                portal = str(row.get("source", "")).strip().lower()
                lid = str(row.get("listing_id", "")).strip() or extract_listing_id(str(row.get("url", "")), portal)
                candidate_keys.append((portal, lid))

            # Query existing records in chunks to scale efficiently
            existing_records: dict[tuple[str, str], dict[str, Any]] = {}
            unique_keys = list(set(candidate_keys))
            chunk_size = 400
            for i in range(0, len(unique_keys), chunk_size):
                chunk = unique_keys[i : i + chunk_size]
                placeholders = ",".join(["(?, ?)"] * len(chunk))
                flat_params = [item for pair in chunk for item in pair]
                cursor.execute(
                    f"SELECT * FROM listing_history WHERE (portal, listing_id) IN (VALUES {placeholders})",
                    flat_params,
                )
                for row in cursor.fetchall():
                    existing_records[(str(row["portal"]), str(row["listing_id"]))] = dict(row)

            updates: list[tuple[Any, ...]] = []
            enriched_records: list[dict[str, Any]] = []

            for _, row in enriched.iterrows():
                portal = str(row.get("source", "")).strip().lower()
                listing_id = str(row.get("listing_id", "")).strip()
                if not listing_id:
                    listing_id = extract_listing_id(str(row.get("url", "")), portal)

                key = (portal, listing_id)
                current_price = float(row.get("price", 0) or 0)
                obs_date = str(row.get("collected_date") or current_date_str).strip()
                url = str(row.get("url", "")).strip()
                neighborhood = str(row.get("neighborhood", "")).strip()
                street = str(row.get("street", "")).strip()
                number = str(row.get("number", "")).strip()
                area_m2 = float(row.get("area_m2", 0) or 0)

                if key in existing_records:
                    rec = existing_records[key]
                    first_seen = str(rec["first_seen"])
                    initial_price = float(rec["initial_price"])
                    last_price = float(rec["latest_price"])

                    if current_price > 0 and current_price != last_price:
                        prev_price = last_price
                        latest_price = current_price
                    else:
                        prev_price = float(rec["previous_price"])
                        latest_price = last_price if last_price > 0 else current_price

                    price_drop = max(0.0, initial_price - latest_price)
                    price_drop_pct = (price_drop / initial_price) if initial_price > 0 else 0.0

                    try:
                        d1 = datetime.fromisoformat(first_seen[:10])
                        d2 = datetime.fromisoformat(obs_date[:10])
                        days_on_market = max(0, (d2 - d1).days)
                    except ValueError:
                        days_on_market = int(rec["days_on_market"])

                    obs_count = int(rec["observations_count"]) + (1 if obs_date != str(rec["last_seen"]) else 0)
                    last_seen = obs_date
                else:
                    first_seen = obs_date
                    last_seen = obs_date
                    initial_price = current_price
                    prev_price = current_price
                    latest_price = current_price
                    price_drop = 0.0
                    price_drop_pct = 0.0
                    days_on_market = 0
                    obs_count = 1

                updates.append((
                    portal, listing_id, url, neighborhood, street, number, area_m2,
                    first_seen, last_seen, initial_price, prev_price, latest_price,
                    price_drop, price_drop_pct, days_on_market, obs_count,
                ))

                enriched_records.append({
                    "listing_id": listing_id,
                    "first_seen": first_seen,
                    "initial_price": initial_price,
                    "previous_price": prev_price,
                    "price_drop": price_drop,
                    "price_drop_pct": price_drop_pct,
                    "days_on_market": days_on_market,
                    "observations_count": obs_count,
                })

            cursor.executemany("""
                INSERT INTO listing_history (
                    portal, listing_id, url, neighborhood, street, number, area_m2,
                    first_seen, last_seen, initial_price, previous_price, latest_price,
                    price_drop, price_drop_pct, days_on_market, observations_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(portal, listing_id) DO UPDATE SET
                    url = excluded.url,
                    neighborhood = excluded.neighborhood,
                    street = excluded.street,
                    number = excluded.number,
                    area_m2 = excluded.area_m2,
                    last_seen = excluded.last_seen,
                    previous_price = excluded.previous_price,
                    latest_price = excluded.latest_price,
                    price_drop = excluded.price_drop,
                    price_drop_pct = excluded.price_drop_pct,
                    days_on_market = excluded.days_on_market,
                    observations_count = excluded.observations_count;
            """, updates)
            conn.commit()

        metrics_df = pd.DataFrame(enriched_records, index=enriched.index)
        for col in metrics_df.columns:
            enriched[col] = metrics_df[col]
        return enriched

    def get_history(self, portal: str | None = None) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT * FROM listing_history"
            params: list[Any] = []
            if portal:
                query += " WHERE portal = ?"
                params.append(portal.lower().strip())
            return pd.read_sql_query(query, conn, params=params)

    def get_price_drops(self, min_drop_pct: float = 0.03) -> pd.DataFrame:
        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT * FROM listing_history WHERE price_drop_pct >= ? ORDER BY price_drop_pct DESC"
            return pd.read_sql_query(query, conn, params=[min_drop_pct])
