"""
Mock email thread scenarios for testing the extraction pipeline.

Each scenario simulates the output of `fetch_thread()` — a dict with:
  {"threadId": str, "messages": [{"id", "from", "to", "cc", "subject", "date", "body", "parsed_body"}, ...]}

These do NOT require Gmail credentials. They feed directly into `run_agent_step_async()`.
"""

from typing import Any, Dict, List


def _make_thread(thread_id: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Helper: wrap messages into a thread dict."""
    for msg in messages:
        msg.setdefault("cc", "")
        msg.setdefault("parsed_body", msg.get("body", ""))
    return {"threadId": thread_id, "messages": messages}


# ─────────────────────────────────────────────
# Scenario 1: Simple staffing request
# ─────────────────────────────────────────────
SCENARIO_1_SIMPLE_STAFFING = _make_thread(
    thread_id="MOCK_THREAD_001",
    messages=[
        {
            "id": "msg_001a",
            "from": "operations@clientcompany.com",
            "to": "scheduling@qstaff.ca",
            "subject": "Staffing Request - Toronto General Hospital - Oct 15",
            "date": "Mon, 14 Oct 2025 09:00:00 -0400",
            "body": (
                "Hi QStaff Team,\n\n"
                "Please schedule staffing for the following:\n\n"
                "Location: Toronto General Hospital\n"
                "Date: October 15, 2025\n"
                "Shift: Day (7am - 3pm)\n"
                "Hours: 8\n"
                "Client: Q2 Management\n\n"
                "Please confirm the names.\n\n"
                "Thanks,\n"
                "Operations Team"
            ),
        },
        {
            "id": "msg_001b",
            "from": "scheduling@qstaff.ca",
            "to": "operations@clientcompany.com",
            "subject": "Re: Staffing Request - Toronto General Hospital - Oct 15",
            "date": "Mon, 14 Oct 2025 14:00:00 -0400",
            "body": (
                "Hi,\n\n"
                "Please confirm the updated list for Toronto General Hospital on Oct 15:\n\n"
                "T244264 Harmandeep Singh Dhindsa 4377558679\n"
                "T243457 Dev Ray 2262605361\n"
                "153257 Beant Kaur Sidhu 6475942588\n\n"
                "Thanks,\n"
                "QStaff Scheduling"
            ),
        },
    ],
)

SCENARIO_1_EXPECTED = {
    "description": "Simple staffing request with 3 named employees",
    "checks": {
        "valid_thread": True,
        "has_requirements": True,
        "min_finalized_employees": 3,
        "expected_shift_date": "2025-10-15",
        "expected_shift_time": "Day",
        "expected_location_contains": "Toronto General Hospital",
    },
}


# ─────────────────────────────────────────────
# Scenario 2: Multi-requirement (Day + Night)
# ─────────────────────────────────────────────
SCENARIO_2_MULTI_REQUIREMENT = _make_thread(
    thread_id="MOCK_THREAD_002",
    messages=[
        {
            "id": "msg_002a",
            "from": "manager@tftclient.com",
            "to": "scheduling@qstaff.ca",
            "subject": "TFT - St. Michael's Hospital - Nov 1 Day and Night",
            "date": "Wed, 30 Oct 2025 08:00:00 -0400",
            "body": (
                "Hello QStaff,\n\n"
                "We need staffing for St. Michael's Hospital on November 1st, 2025:\n\n"
                "DAY SHIFT (7am - 3pm):\n"
                "Need 2 workers\n\n"
                "NIGHT SHIFT (11pm - 7am):\n"
                "Need 1 worker\n\n"
                "Client code: TFT\n\n"
                "Please send the names.\n\n"
                "Regards,\n"
                "TFT Manager"
            ),
        },
        {
            "id": "msg_002b",
            "from": "scheduling@qstaff.ca",
            "to": "manager@tftclient.com",
            "subject": "Re: TFT - St. Michael's Hospital - Nov 1 Day and Night",
            "date": "Wed, 30 Oct 2025 15:00:00 -0400",
            "body": (
                "Hi,\n\n"
                "Confirmed for St. Michael's Hospital on Nov 1:\n\n"
                "Day Shift:\n"
                "Mansi Dhanani\n"
                "Aaryan Rawal\n\n"
                "Night Shift:\n"
                "Priya Patel\n\n"
                "Thanks,\n"
                "QStaff"
            ),
        },
    ],
)

SCENARIO_2_EXPECTED = {
    "description": "Multi-requirement thread with Day and Night shifts",
    "checks": {
        "valid_thread": True,
        "has_requirements": True,
        "min_requirements_count": 2,
        "expected_shift_date": "2025-11-01",
        "expected_location_contains": "St. Michael",
    },
}


# ─────────────────────────────────────────────
# Scenario 3: Cancellation thread
# ─────────────────────────────────────────────
SCENARIO_3_CANCELLATION = _make_thread(
    thread_id="MOCK_THREAD_003",
    messages=[
        {
            "id": "msg_003a",
            "from": "ops@q2management.com",
            "to": "scheduling@qstaff.ca",
            "subject": "Staffing for City Hall - Oct 20",
            "date": "Fri, 18 Oct 2025 10:00:00 -0400",
            "body": (
                "Hi,\n\n"
                "Please schedule 3 workers for City Hall on October 20, 2025.\n"
                "Day shift, 8 hours.\n"
                "Client: Q2\n\n"
                "Thanks"
            ),
        },
        {
            "id": "msg_003b",
            "from": "scheduling@qstaff.ca",
            "to": "ops@q2management.com",
            "subject": "Re: Staffing for City Hall - Oct 20",
            "date": "Fri, 18 Oct 2025 14:00:00 -0400",
            "body": (
                "Confirmed:\n"
                "Rahul Sharma\n"
                "Neha Gupta\n"
                "Amit Verma\n"
            ),
        },
        {
            "id": "msg_003c",
            "from": "ops@q2management.com",
            "to": "scheduling@qstaff.ca",
            "subject": "Re: Staffing for City Hall - Oct 20",
            "date": "Sat, 19 Oct 2025 08:00:00 -0400",
            "body": (
                "Hi QStaff,\n\n"
                "Please cancel this request. We don't need them anymore.\n"
                "Do not send anyone on Oct 20.\n\n"
                "Thanks"
            ),
        },
    ],
)

SCENARIO_3_EXPECTED = {
    "description": "Cancellation thread — status should be 'delete'",
    "checks": {
        "valid_thread": True,
        "has_requirements": True,
        "expected_status": "delete",
        "expected_shift_date": "2025-10-20",
    },
}


# ─────────────────────────────────────────────
# Scenario 4: Employee replacement mid-thread
# ─────────────────────────────────────────────
SCENARIO_4_REPLACEMENT = _make_thread(
    thread_id="MOCK_THREAD_004",
    messages=[
        {
            "id": "msg_004a",
            "from": "client@vasservices.com",
            "to": "scheduling@qstaff.ca",
            "subject": "VAS - Warehouse Staffing Nov 5",
            "date": "Mon, 03 Nov 2025 09:00:00 -0500",
            "body": (
                "Hi,\n\n"
                "Please schedule staffing for our warehouse on November 5, 2025.\n"
                "Day shift, 8 hours.\n"
                "Client: VAS\n\n"
                "Thanks"
            ),
        },
        {
            "id": "msg_004b",
            "from": "scheduling@qstaff.ca",
            "to": "client@vasservices.com",
            "subject": "Re: VAS - Warehouse Staffing Nov 5",
            "date": "Mon, 03 Nov 2025 13:00:00 -0500",
            "body": (
                "Confirmed for Nov 5 Day:\n"
                "Rajesh Kumar\n"
                "Simran Kaur\n\n"
                "Thanks,\n"
                "QStaff"
            ),
        },
        {
            "id": "msg_004c",
            "from": "scheduling@qstaff.ca",
            "to": "client@vasservices.com",
            "subject": "Re: VAS - Warehouse Staffing Nov 5",
            "date": "Tue, 04 Nov 2025 10:00:00 -0500",
            "body": (
                "Hi,\n\n"
                "Rajesh Kumar won't be able to come on Nov 5. "
                "Sending his replacement Vikram Singh.\n\n"
                "Updated list:\n"
                "Vikram Singh\n"
                "Simran Kaur\n\n"
                "Thanks,\n"
                "QStaff"
            ),
        },
    ],
)

SCENARIO_4_EXPECTED = {
    "description": "Employee replacement — Rajesh replaced by Vikram",
    "checks": {
        "valid_thread": True,
        "has_requirements": True,
        "expected_finalized_must_include": ["Vikram Singh", "Simran Kaur"],
        "expected_finalized_must_exclude": ["Rajesh Kumar"],
        "expected_shift_date": "2025-11-05",
    },
}


# ─────────────────────────────────────────────
# Scenario 5: Non-staffing email (should be invalid)
# ─────────────────────────────────────────────
SCENARIO_5_NON_STAFFING = _make_thread(
    thread_id="MOCK_THREAD_005",
    messages=[
        {
            "id": "msg_005a",
            "from": "hr@clientcompany.com",
            "to": "compliance@qstaff.ca",
            "subject": "Employee Late Report - Oct 10",
            "date": "Thu, 10 Oct 2025 16:00:00 -0400",
            "body": (
                "Hi QStaff,\n\n"
                "This is to inform you that John Doe arrived 45 minutes late "
                "to his shift today at the downtown location.\n\n"
                "Please address this with the worker.\n\n"
                "Thanks,\n"
                "HR Department"
            ),
        },
    ],
)

SCENARIO_5_EXPECTED = {
    "description": "Non-staffing email (late report) — should be invalid",
    "checks": {
        "valid_thread": False,
    },
}


# ─────────────────────────────────────────────
# Scenario 6: Employee IDs and phone numbers
# ─────────────────────────────────────────────
SCENARIO_6_IDS_AND_PHONES = _make_thread(
    thread_id="MOCK_THREAD_006",
    messages=[
        {
            "id": "msg_006a",
            "from": "dispatch@sqclient.com",
            "to": "scheduling@qstaff.ca",
            "subject": "SQ Staffing - Convention Centre - Dec 1",
            "date": "Fri, 29 Nov 2025 08:00:00 -0500",
            "body": (
                "Hi QStaff,\n\n"
                "Please schedule staffing for the Convention Centre on December 1, 2025.\n"
                "Afternoon shift (12pm - 8pm), 8 hours.\n"
                "Client: SQ\n\n"
                "Please send the names with IDs.\n\n"
                "Thanks"
            ),
        },
        {
            "id": "msg_006b",
            "from": "scheduling@qstaff.ca",
            "to": "dispatch@sqclient.com",
            "subject": "Re: SQ Staffing - Convention Centre - Dec 1",
            "date": "Fri, 29 Nov 2025 16:00:00 -0500",
            "body": (
                "Please confirm the below list for Convention Centre Dec 1 Afternoon:\n\n"
                "T244264 Harmandeep Singh Dhindsa 4377558679\n"
                "N/A Riddhi Arora 2262601134\n"
                "153257 Beant Kaur Sidhu 6475942588\n"
                "T243457 Dev Ray 2262605361\n\n"
                "Thanks,\n"
                "QStaff Scheduling"
            ),
        },
    ],
)

SCENARIO_6_EXPECTED = {
    "description": "IDs and phone numbers must be preserved correctly",
    "checks": {
        "valid_thread": True,
        "has_requirements": True,
        "min_finalized_employees": 4,
        "expected_shift_time": "Afternoon",
        "expected_shift_date": "2025-12-01",
        "expected_location_contains": "Convention Centre",
    },
}


# ─────────────────────────────────────────────
# Scenario 7: Supervisor should NOT be in employees
# ─────────────────────────────────────────────
SCENARIO_7_SUPERVISOR_EXCLUSION = _make_thread(
    thread_id="MOCK_THREAD_007",
    messages=[
        {
            "id": "msg_007a",
            "from": "admin@clientsite.com",
            "to": "scheduling@qstaff.ca",
            "subject": "Staffing Request - Industrial Park - Oct 25",
            "date": "Wed, 23 Oct 2025 09:00:00 -0400",
            "body": (
                "Hi QStaff,\n\n"
                "Please schedule 2 workers for Industrial Park on October 25, 2025.\n"
                "Day shift, 8 hours.\n"
                "Client: Q2\n\n"
                "Reporting Supervisor: Michael Johnson\n"
                "Site Supervisor cell: 416-555-1234\n\n"
                "Please confirm names.\n\n"
                "Thanks"
            ),
        },
        {
            "id": "msg_007b",
            "from": "scheduling@qstaff.ca",
            "to": "admin@clientsite.com",
            "subject": "Re: Staffing Request - Industrial Park - Oct 25",
            "date": "Wed, 23 Oct 2025 15:00:00 -0400",
            "body": (
                "Confirmed for Industrial Park Oct 25 Day:\n\n"
                "Anika Patel\n"
                "Rajan Mehta\n\n"
                "Reporting Supervisor: Michael Johnson\n\n"
                "Thanks,\n"
                "QStaff"
            ),
        },
    ],
)

SCENARIO_7_EXPECTED = {
    "description": "Supervisor 'Michael Johnson' must NOT appear in finalized_employees",
    "checks": {
        "valid_thread": True,
        "has_requirements": True,
        "min_finalized_employees": 2,
        "expected_finalized_must_exclude": ["Michael Johnson"],
        "expected_shift_date": "2025-10-25",
        "expected_location_contains": "Industrial Park",
    },
}


# ─────────────────────────────────────────────
# All scenarios registry
# ─────────────────────────────────────────────
ALL_SCENARIOS = [
    ("scenario_1_simple_staffing", SCENARIO_1_SIMPLE_STAFFING, SCENARIO_1_EXPECTED),
    ("scenario_2_multi_requirement", SCENARIO_2_MULTI_REQUIREMENT, SCENARIO_2_EXPECTED),
    ("scenario_3_cancellation", SCENARIO_3_CANCELLATION, SCENARIO_3_EXPECTED),
    ("scenario_4_replacement", SCENARIO_4_REPLACEMENT, SCENARIO_4_EXPECTED),
    ("scenario_5_non_staffing", SCENARIO_5_NON_STAFFING, SCENARIO_5_EXPECTED),
    ("scenario_6_ids_and_phones", SCENARIO_6_IDS_AND_PHONES, SCENARIO_6_EXPECTED),
    ("scenario_7_supervisor_exclusion", SCENARIO_7_SUPERVISOR_EXCLUSION, SCENARIO_7_EXPECTED),
]
