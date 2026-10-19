from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.services.csv_export import records_to_csv
from app.services.store import DocumentStore, get_store

router = APIRouter(tags=["export"])


@router.get("/api/export.csv")
def export_csv(reviewed_only: bool = False, store: DocumentStore = Depends(get_store)) -> Response:
    """Export the current records (including any corrections). UTF-8 with BOM for Excel."""
    text = records_to_csv(store.list(), reviewed_only=reviewed_only)
    return Response(
        content=("\ufeff" + text).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="invoicelens_export.csv"'},
    )
