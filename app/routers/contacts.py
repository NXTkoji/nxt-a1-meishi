from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth import verify_api_key
from app.models.responses import ContactSearchResult, SearchResponse
from app.services.odoo_sync import search_odoo_contacts
from app.services.google_contacts import search_google_contacts

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


@router.get("/contacts/search", response_model=SearchResponse)
async def search_contacts(q: str = Query(..., min_length=1)):
    """Search existing contacts across Odoo and Google."""
    odoo_results = await search_odoo_contacts(q)
    google_results = await search_google_contacts(q)

    all_results = [
        ContactSearchResult(**r)
        for r in odoo_results + google_results
    ]

    return SearchResponse(results=all_results)
