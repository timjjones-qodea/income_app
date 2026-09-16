#!/usr/bin/env python3
"""Audit and remove cross-import duplicate income transactions.

This is deliberately conservative: a candidate must match account, date, income
type, penny-rounded amount, description and security, and copies must originate
from different imports. The newest transaction is retained.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


INCOME_TYPES = ("DIVIDEND", "INTEREST", "GROSS_INTEREST")


def income_family(transaction_type: str) -> str:
    """Treat legacy and current AJ Bell interest labels as the same income kind."""
    return "INTEREST" if transaction_type in {"INTEREST", "GROSS_INTEREST"} else transaction_type


def money(value: object) -> str:
    return f"{Decimal(str(value or 0)).quantize(Decimal('0.01')):.2f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/app/data/retirement_income.db")
    parser.add_argument("--backup-dir", default="/app/data/backups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    placeholders = ",".join("?" for _ in INCOME_TYPES)
    rows = conn.execute(
        f"""
        SELECT t.*, a.account_name
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.transaction_type IN ({placeholders})
        ORDER BY t.id
        """,
        INCOME_TYPES,
    ).fetchall()

    groups: dict[tuple[object, ...], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        key = (
            row["account_id"],
            row["transaction_date"],
            income_family(row["transaction_type"]),
            money(row["net_amount"]),
            " ".join(row["description"].split()),
            row["security_id"],
        )
        groups[key].append(row)

    candidates = []
    for key, copies in groups.items():
        import_ids = {row["source_import_id"] for row in copies}
        if len(copies) < 2 or len(import_ids) < 2:
            continue
        keep = max(copies, key=lambda row: row["id"])
        remove = [row for row in copies if row["id"] != keep["id"]]
        candidates.append((key, keep, remove))

    account_summary: dict[str, dict[str, object]] = defaultdict(
        lambda: {"duplicate_groups": 0, "rows_to_remove": 0, "amount_to_remove": Decimal("0")}
    )
    details = []
    for key, keep, remove in candidates:
        summary = account_summary[keep["account_name"]]
        summary["duplicate_groups"] += 1
        summary["rows_to_remove"] += len(remove)
        summary["amount_to_remove"] += sum((Decimal(money(row["net_amount"])) for row in remove), Decimal("0"))
        details.append(
            {
                "account": keep["account_name"],
                "date": keep["transaction_date"],
                "type": keep["transaction_type"],
                "removed_types": sorted({row["transaction_type"] for row in remove}),
                "amount": money(keep["net_amount"]),
                "description": keep["description"],
                "security_id": keep["security_id"],
                "retained_transaction_id": keep["id"],
                "retained_import_id": keep["source_import_id"],
                "removed": [
                    {"transaction_id": row["id"], "import_id": row["source_import_id"], "row_hash": row["source_row_hash"]}
                    for row in remove
                ],
            }
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = {
        "created_at_utc": timestamp,
        "database": str(db_path),
        "mode": "apply" if args.apply else "audit",
        "candidate_groups": len(candidates),
        "rows_to_remove": sum(len(remove) for _, _, remove in candidates),
        "account_summary": {
            name: {
                **values,
                "amount_to_remove": money(values["amount_to_remove"]),
            }
            for name, values in sorted(account_summary.items())
        },
        "details": details,
    }

    if args.apply:
        backup_dir = Path(args.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"retirement_income_before_dedupe_{timestamp}.db"
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()

        manifest_path = backup_dir / f"income_dedupe_{timestamp}.json"
        manifest["backup_path"] = str(backup_path)
        manifest["manifest_path"] = str(manifest_path)
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n")

        affected_jobs: set[int] = set()
        with conn:
            for _, keep, remove in candidates:
                for row in remove:
                    affected_jobs.add(row["source_import_id"])
                    note = (
                        "Superseded as an economic duplicate during audited cleanup "
                        f"{timestamp}; retained transaction ID {keep['id']}."
                    )
                    conn.execute(
                        """
                        UPDATE import_rows
                        SET committed = 0,
                            warnings = CASE
                                WHEN warnings IS NULL OR warnings = '' THEN ?
                                ELSE warnings || ' | ' || ?
                            END
                        WHERE import_job_id = ? AND row_hash = ?
                        """,
                        (note, note, row["source_import_id"], row["source_row_hash"]),
                    )
                    conn.execute("DELETE FROM transactions WHERE id = ?", (row["id"],))
            for job_id in affected_jobs:
                conn.execute(
                    """
                    UPDATE import_jobs
                    SET warning_count = (
                        SELECT COUNT(*) FROM import_rows
                        WHERE import_job_id = ? AND warnings IS NOT NULL AND warnings != ''
                    )
                    WHERE id = ?
                    """,
                    (job_id, job_id),
                )

        remaining = conn.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT account_id, transaction_date,
                       CASE WHEN transaction_type IN ('INTEREST', 'GROSS_INTEREST')
                            THEN 'INTEREST' ELSE transaction_type END AS income_family,
                       ROUND(net_amount, 2), description, security_id
                FROM transactions
                WHERE transaction_type IN ({placeholders})
                GROUP BY account_id, transaction_date, income_family,
                         ROUND(net_amount, 2), description, security_id
                HAVING COUNT(*) > 1 AND COUNT(DISTINCT source_import_id) > 1
            )
            """,
            INCOME_TYPES,
        ).fetchone()[0]
        manifest["remaining_cross_import_duplicate_groups"] = remaining
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n")

    print(json.dumps(manifest, indent=2, default=str))


if __name__ == "__main__":
    main()
