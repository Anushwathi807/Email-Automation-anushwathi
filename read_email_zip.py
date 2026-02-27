from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from agent.tools import fetch_thread, get_creds


def _parse_iso_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid ISO date: {value} (expected YYYY-MM-DD)") from e


def _parse_email_date_to_iso(date_header_value: str) -> str:
    """
    Best-effort parse for Gmail "Date" header values into YYYY-MM-DD.
    Returns "" if parsing fails.
    """
    if not date_header_value:
        return ""
    try:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(date_header_value)
        if isinstance(parsed, dt.datetime):
            return parsed.date().isoformat()
    except Exception:
        pass
    try:
        # Some providers include ISO-like dates
        parsed2 = dt.datetime.fromisoformat(date_header_value)
        return parsed2.date().isoformat()
    except Exception:
        return ""


def _retrying_execute(request, *, retries: int = 5, base_sleep_s: float = 0.75):
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            return request.execute()
        except Exception as e:  # googleapiclient raises HttpError; keep broad for portability
            last_err = e
            # Backoff with jitter (no random import: deterministic-ish)
            time.sleep(base_sleep_s * (2**attempt))
    raise last_err  # type: ignore[misc]


def list_threads_in_range(
    service,
    *,
    start_date_inclusive: dt.date,
    end_date_exclusive: dt.date,
    max_threads: Optional[int] = None,
) -> List[str]:
    """
    Returns thread IDs with messages between [start_date_inclusive, end_date_exclusive).
    Uses Gmail query syntax: after:<YYYY-MM-DD> before:<YYYY-MM-DD>.
    """
    query = f"after:{start_date_inclusive.isoformat()} before:{end_date_exclusive.isoformat()}"
    thread_ids: List[str] = []
    page_token: Optional[str] = None

    while True:
        req = service.users().threads().list(
            userId="me",
            q=query,
            maxResults=500,
            pageToken=page_token,
        )
        res = _retrying_execute(req)
        thread_ids.extend([t["id"] for t in res.get("threads", []) if "id" in t])

        if max_threads is not None and len(thread_ids) >= max_threads:
            return thread_ids[:max_threads]

        page_token = res.get("nextPageToken")
        if not page_token:
            return thread_ids


def _thread_passes_filters(thread_data: Dict[str, Any]) -> bool:
    """
    Mirror agent.tools.get_threads_for_date filtering:
    - Skip threads initiated by @qstaff.ca (based on first remaining message after message-level filtering)
    - Skip empty threads (all messages filtered)
    """
    messages = thread_data.get("messages") or []
    if not messages:
        return False
    first_sender = (messages[0].get("from") or "").lower()
    if "@qstaff.ca" in first_sender:
        return False
    return True


def dump_threads(
    *,
    out_path: str,
    fmt: str,
    group_by_date: bool,
    start_date_inclusive: dt.date,
    end_date_exclusive: dt.date,
    max_threads: Optional[int],
) -> Tuple[int, int]:
    """
    Returns (written_threads, skipped_threads).
    """
    from googleapiclient.discovery import build

    creds = get_creds()
    service = build("gmail", "v1", credentials=creds)

    thread_ids = list_threads_in_range(
        service,
        start_date_inclusive=start_date_inclusive,
        end_date_exclusive=end_date_exclusive,
        max_threads=max_threads,
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    meta = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "range": {
            "start_date_inclusive": start_date_inclusive.isoformat(),
            "end_date_exclusive": end_date_exclusive.isoformat(),
        },
        "gmail_query": f"after:{start_date_inclusive.isoformat()} before:{end_date_exclusive.isoformat()}",
        "filters": {
            "skip_threads_initiated_by_domain": "@qstaff.ca",
            "skip_messages_sender_domain_equals_primary_recipient_domain": True,
        },
        "group_by_date": group_by_date,
        "thread_count_listed": len(thread_ids),
        "max_threads": max_threads,
        "format": fmt,
        "schema": {
            "thread_record": {
                "threadId": "str",
                "messages": "list[{id, from, to, cc, subject, date, body}]",
                "thread_subject": "str|optional",
                "thread_date": "YYYY-MM-DD|optional",
            }
        },
    }

    written = 0
    skipped = 0

    if fmt == "jsonl" and group_by_date:
        raise ValueError("--group-by-date requires --format json")

    if fmt == "jsonl":
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "meta", **meta}, ensure_ascii=False) + "\n")
            for idx, tid in enumerate(thread_ids, start=1):
                try:
                    thread_data = fetch_thread(service, tid)
                except Exception as e:
                    f.write(
                        json.dumps(
                            {"type": "error", "threadId": tid, "stage": "fetch_thread", "error": str(e)},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    skipped += 1
                    continue

                if not _thread_passes_filters(thread_data):
                    skipped += 1
                    continue

                messages = thread_data.get("messages", []) or []
                thread_subject = (messages[0].get("subject") or "") if messages else ""
                thread_date = _parse_email_date_to_iso((messages[0].get("date") or "") if messages else "")

                record: Dict[str, Any] = {
                    "threadId": tid,
                    "messages": thread_data.get("messages", []),
                    "thread_subject": thread_subject,
                    "thread_date": thread_date,
                }

                f.write(json.dumps({"type": "thread", **record}, ensure_ascii=False) + "\n")
                written += 1

                if idx % 25 == 0:
                    print(f"[progress] fetched {idx}/{len(thread_ids)} threads; wrote={written}, skipped={skipped}")

    elif fmt == "json":
        threads: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        by_date: Dict[str, Dict[str, Any]] = {}

        for idx, tid in enumerate(thread_ids, start=1):
            try:
                thread_data = fetch_thread(service, tid)
            except Exception as e:
                errors.append({"threadId": tid, "stage": "fetch_thread", "error": str(e)})
                skipped += 1
                continue

            if not _thread_passes_filters(thread_data):
                skipped += 1
                continue

            messages = thread_data.get("messages", []) or []
            thread_subject = (messages[0].get("subject") or "") if messages else ""
            thread_date = _parse_email_date_to_iso((messages[0].get("date") or "") if messages else "")

            record: Dict[str, Any] = {
                "threadId": tid,
                "messages": thread_data.get("messages", []),
                "thread_subject": thread_subject,
                "thread_date": thread_date,
            }

            if group_by_date:
                # If the "Date" header can't be parsed, bucket under "unknown".
                bucket = thread_date or "unknown"
                if bucket not in by_date:
                    by_date[bucket] = {}
                by_date[bucket][tid] = record
            else:
                threads.append(record)
            written += 1

            if idx % 25 == 0:
                print(f"[progress] fetched {idx}/{len(thread_ids)} threads; wrote={written}, skipped={skipped}")

        payload = {
            "meta": meta,
            "threads": threads if not group_by_date else [],
            "by_date": by_date if group_by_date else {},
            "errors": errors,
            "stats": {"written": written, "skipped": skipped, "errors": len(errors)},
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    return written, skipped


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dump raw Gmail threads (subject/date/body) using the same Gmail parsing/filtering as the API."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="Number of days back from today (default: 60).",
    )
    parser.add_argument(
        "--start",
        type=_parse_iso_date,
        default=None,
        help="Override start date (YYYY-MM-DD). If set, --days is ignored.",
    )
    parser.add_argument(
        "--end",
        type=_parse_iso_date,
        default=None,
        help="Override end date (YYYY-MM-DD). Use --end-inclusive to include this day.",
    )
    parser.add_argument(
        "--end-inclusive",
        action="store_true",
        help="Treat --end as inclusive (end_exclusive = end + 1 day).",
    )
    parser.add_argument(
        "--out",
        default=os.path.join("email_dumps", "raw_email_threads_by_date.json"),
        help="Output path (default: email_dumps/raw_email_threads_by_date.json).",
    )
    parser.add_argument(
        "--format",
        choices=["jsonl", "json"],
        default="json",
        help="Output format: jsonl (streaming) or json (single file). Default: json.",
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--group-by-date",
        dest="group_by_date",
        action="store_true",
        help='Group output by date as: {"by_date": {"YYYY-MM-DD": {"<threadId>": {...}}}} (json only).',
    )
    group.add_argument(
        "--no-group-by-date",
        dest="group_by_date",
        action="store_false",
        help="Do not group by date (writes a flat threads[] list).",
    )
    parser.set_defaults(group_by_date=True)

    parser.add_argument(
        "--max-threads",
        type=int,
        default=None,
        help="Limit the number of listed threads (useful for quick samples).",
    )

    args = parser.parse_args(argv)

    today = dt.date.today()
    if args.end:
        end_date_exclusive = args.end + dt.timedelta(days=1 if args.end_inclusive else 0)
    else:
        end_date_exclusive = today + dt.timedelta(days=1)
    start_date_inclusive = args.start or (end_date_exclusive - dt.timedelta(days=args.days))

    if start_date_inclusive >= end_date_exclusive:
        print("start date must be before end date", file=sys.stderr)
        return 2

    print(
        f"[info] range: {start_date_inclusive.isoformat()} .. {end_date_exclusive.isoformat()} (exclusive)"
        f" | format={args.format} | group_by_date={bool(args.group_by_date)}"
    )
    print(f"[info] output: {args.out}")

    written, skipped = dump_threads(
        out_path=args.out,
        fmt=args.format,
        group_by_date=bool(args.group_by_date),
        start_date_inclusive=start_date_inclusive,
        end_date_exclusive=end_date_exclusive,
        max_threads=args.max_threads,
    )

    print(f"[done] wrote={written} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
