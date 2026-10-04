"""投影 projection: the single claim pin shown by wall card, detail and mine.

Every read endpoint attaches the same `pin` dict via `attach`, so the winner
of a claim race is rendered identically on all three routes (三路同钉).
"""

def pin_for(row: dict) -> dict:
    status = row.get("status") or "open"
    claimer = row.get("claimer")
    if status == "claimed" and claimer:
        label = f"{claimer} 已认领"
    elif status == "fulfilled":
        label = "已核销"
    elif status == "released":
        label = "已释放·可再认领"
    else:
        label = "可认领"
    return {
        "state": status,
        "claimer": claimer,
        "pinned": status == "claimed" and bool(claimer),
        "label": label,
    }

def attach(row: dict) -> dict:
    out = dict(row)
    out["pin"] = pin_for(row)
    return out

def attach_all(rows) -> list:
    return [attach(r) for r in rows]
