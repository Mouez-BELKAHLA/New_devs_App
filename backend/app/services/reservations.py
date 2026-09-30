from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List

from sqlalchemy import text
from app.core.database_pool import db_pool  # shared instance

CENT = Decimal("0.01")

# Debug mock data, keyed by tenant so it can't leak across tenants.
MOCK_DATA = {
    ("tenant-a", "prop-001"): {"total": "1000.00", "count": 3},
    ("tenant-a", "prop-002"): {"total": "4975.50", "count": 4},
    ("tenant-a", "prop-003"): {"total": "6100.50", "count": 2},
    ("tenant-b", "prop-004"): {"total": "1776.50", "count": 4},
    ("tenant-b", "prop-005"): {"total": "3256.00", "count": 3},
}


async def calculate_monthly_revenue(property_id: str, month: int, year: int, tenant_id: str, db_session=None) -> Decimal:
    """
    Calculates revenue for a specific month, using the PROPERTY's local
    timezone for the month boundaries.
    """

    start_date = datetime(year, month, 1)
    if month < 12:
        end_date = datetime(year, month + 1, 1)
    else:
        end_date = datetime(year + 1, 1, 1)

    print(f"DEBUG: Querying revenue for {property_id} from {start_date} to {end_date}")

    # check_in_date is timestamptz; AT TIME ZONE converts it to the property's
    # local time, so it is compared against local month boundaries.
    query = text("""
        SELECT COALESCE(SUM(r.total_amount), 0) AS total
        FROM reservations r
        JOIN properties p ON p.id = r.property_id AND p.tenant_id = r.tenant_id
        WHERE r.property_id = :property_id
        AND r.tenant_id = :tenant_id
        AND (r.check_in_date AT TIME ZONE p.timezone) >= CAST(:start_date AS timestamp)
        AND (r.check_in_date AT TIME ZONE p.timezone) < CAST(:end_date AS timestamp)
    """)

    await db_pool.initialize()
    async with db_pool.get_session() as session:
        result = await session.execute(query, {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "start_date": start_date,
            "end_date": end_date,
        })
        row = result.fetchone()

    return Decimal(str(row.total)).quantize(CENT, rounding=ROUND_HALF_UP)


async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    try:
        await db_pool.initialize()
        if not db_pool.session_factory:
            raise Exception("Database pool not available")

        async with db_pool.get_session() as session:
            result = await session.execute(
                text("""
                    SELECT COALESCE(SUM(total_amount), 0) AS total_revenue,
                           COUNT(*) AS reservation_count
                    FROM reservations
                    WHERE property_id = :property_id AND tenant_id = :tenant_id
                """),
                {"property_id": property_id, "tenant_id": tenant_id},
            )
            row = result.fetchone()

        total = Decimal(str(row.total_revenue)).quantize(CENT, rounding=ROUND_HALF_UP)
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": str(total),
            "currency": "USD",
            "count": row.reservation_count,
        }

    except Exception as e:
        print(f"Database error for {property_id} (tenant: {tenant_id}): {e}")

        # Debug fallback when DB is unavailable. Only returns data that
        # belongs to this tenant; anything else gets zeros.
        mock = MOCK_DATA.get((tenant_id, property_id), {"total": "0.00", "count": 0})
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": mock["total"],
            "currency": "USD",
            "count": mock["count"],
        }