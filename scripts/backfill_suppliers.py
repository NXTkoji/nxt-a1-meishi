"""
Backfill existing suppliers into the party-code model.

Usage (from the project root, with .env pointing at the real Odoo and Drive):

    python3 scripts/backfill_suppliers.py                        # dry run: writes the CSV report only
    python3 scripts/backfill_suppliers.py --report my.csv        # choose the report path
    python3 scripts/backfill_suppliers.py --apply                # do the rows marked "apply"
    python3 scripts/backfill_suppliers.py --apply --decisions my.csv
                                                                 # use the decisions you edited in the report

The report has one row per proposed change. `decision` is apply (done on --apply), confirm
(needs you: change it to apply, or to create on a name-match row for a new company), or skip.
Rows are matched by row_id, so you can re-run the dry run and keep your decisions. Only partners
of the company in ODOO_COMPANY_ID (and shared ones) are read; other companies are only counted.
Re-running is safe: every write re-checks the current Odoo value first.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.services import supplier_backfill as bf
from app.services.drive_folders import DriveFolders
from app.services.odoo_codes import OdooCodes


async def main(args: argparse.Namespace) -> int:
    root = settings.google_drive_suppliers_folder_id
    if not root:
        print("GOOGLE_DRIVE_SUPPLIERS_FOLDER_ID is not set in .env", file=sys.stderr)
        return 2
    odoo = OdooCodes.connect()
    print(f"Odoo company in scope: {odoo.company_id}")
    async with await DriveFolders.from_settings() as drive:
        plan = await bf.build_plan(odoo, drive, root)

    decisions = bf.read_decisions(args.decisions) if args.decisions else None
    results = None
    if args.apply:
        results = await asyncio.to_thread(bf.apply_plan, odoo, plan, decisions)
    report = args.report or f"backfill_suppliers_{date.today().isoformat()}{'_applied' if args.apply else ''}.csv"
    bf.write_report(plan, report, results)

    print("\nCounts:", ", ".join(f"{k}={v}" for k, v in plan.counts.items()))
    shown = Counter((r.action, decisions.get(r.row_id, r.decision) if decisions else r.decision) for r in plan.rows)
    for (action, decision), n in sorted(shown.items()):
        print(f"  {action:16} {decision:8} {n}")
    if results:
        print("\nResults:", dict(Counter(v.split(":")[0] for v in results.values())))
    print(f"\nReport written to {report}" + ("" if args.apply else " (dry run: nothing was changed)"))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write to Odoo (default: dry run)")
    parser.add_argument("--report", help="CSV report path")
    parser.add_argument("--decisions", help="a report you edited; its decision column wins")
    sys.exit(asyncio.run(main(parser.parse_args())))
