"""API views. Figures are copied from the loss engine payload."""

from django.http import JsonResponse

from .engine import get_payload


def portfolio(_request):
    return JsonResponse(get_payload())


def buildings(_request):
    return JsonResponse({"buildings": get_payload()["buildings"]})


def building_detail(_request, loc_id):
    building = next((item for item in get_payload()["buildings"] if item["loc_id"] == loc_id), None)
    if building is None:
        return JsonResponse({"detail": "Building not found."}, status=404)
    return JsonResponse(building)


def loss_curve(request):
    payload = get_payload()
    assumption = request.GET.get("assumption", "reference")
    summary = payload["tier_summary"].get(assumption)
    if summary is None:
        return JsonResponse({"detail": "Unknown damage assumption."}, status=404)
    points = []
    for tier in payload["tiers"]:
        row = summary[tier["id"]]
        points.append(
            {
                "tier": tier["id"],
                "label": tier["label"],
                "assumed_return_period_years": tier["assumed_return_period_years"],
                "return_period_basis": tier["return_period_basis"],
                "portfolio_loss_kes": row["portfolio_loss_kes"],
                "loss_pct_portfolio": row["loss_pct_portfolio"],
                "affected_buildings": row["affected_buildings"],
                "tiv_kes": row["tiv_kes"],
            }
        )
    return JsonResponse({"assumption": assumption, "points": points})


def hotspots(_request):
    return JsonResponse({"hotspots": get_payload()["hotspots"]})
