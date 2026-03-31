from __future__ import annotations

import json

from dakota_gateway.state_db import exec1, now_ms, query_all, query_one
from control.services.scenario_shared import normalize_observability_filters, normalize_scenario_tags


def list_analytics_scenarios(
    con,
    scope: str = "observability",
    user_id: int | None = None,
    *,
    visibility: str = "",
    tag: str = "",
) -> list[dict]:
    rows = query_all(
        con,
        """
        SELECT s.id, s.name, s.description, s.scope, s.visibility, s.tags_csv, s.filters_json, s.created_by, s.created_at_ms, s.updated_at_ms,
               CASE WHEN f.user_id IS NULL THEN 0 ELSE 1 END AS is_favorite,
               u.username AS created_by_username
        FROM analytics_scenarios s
        LEFT JOIN users u ON u.id = s.created_by
        LEFT JOIN analytics_scenario_favorites f ON f.scenario_id = s.id AND f.user_id = ?
        WHERE s.scope=?
          AND (? IS NULL OR s.visibility='shared' OR s.created_by=?)
        ORDER BY is_favorite DESC, s.updated_at_ms DESC, s.id DESC
        """,
        (user_id, scope, user_id, user_id),
    )
    scenarios = []
    visibility_filter = str(visibility or "").strip().lower()
    tag_filter = str(tag or "").strip().lower()
    for row in rows:
        item = dict(row)
        try:
            filters = json.loads(item.pop("filters_json") or "{}")
        except Exception:
            filters = {}
        item["filters"] = normalize_observability_filters(filters)
        item["tags"] = normalize_scenario_tags(item.pop("tags_csv") or "")
        item["is_favorite"] = bool(int(item.get("is_favorite") or 0))
        if visibility_filter and str(item.get("visibility") or "").lower() != visibility_filter:
            continue
        if tag_filter and not any(tag_filter in str(tag_name).lower() for tag_name in item["tags"]):
            continue
        scenarios.append(item)
    return scenarios


def save_analytics_scenario(
    con,
    *,
    name: str,
    description: str = "",
    scope: str = "observability",
    visibility: str = "private",
    tags=None,
    filters: dict | None = None,
    created_by: int | None = None,
) -> int:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("nome do cenário não informado")
    clean_description = str(description or "").strip()
    clean_visibility = str(visibility or "private").strip().lower()
    if clean_visibility not in {"private", "shared"}:
        raise ValueError("visibility inválida")
    clean_tags = normalize_scenario_tags(tags)
    normalized_filters = normalize_observability_filters(filters)
    existing = query_one(con, "SELECT id FROM analytics_scenarios WHERE name=? AND scope=?", (clean_name, scope))
    ts = now_ms()
    if existing:
        con.execute(
            """
            UPDATE analytics_scenarios
            SET description=?, visibility=?, tags_csv=?, filters_json=?, updated_at_ms=?
            WHERE id=?
            """,
            (
                clean_description or None,
                clean_visibility,
                ",".join(clean_tags) or None,
                json.dumps(normalized_filters, ensure_ascii=False),
                ts,
                int(existing["id"]),
            ),
        )
        return int(existing["id"])
    return exec1(
        con,
        """
        INSERT INTO analytics_scenarios(name, description, scope, visibility, tags_csv, filters_json, created_by, created_at_ms, updated_at_ms)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            clean_name,
            clean_description or None,
            scope,
            clean_visibility,
            ",".join(clean_tags) or None,
            json.dumps(normalized_filters, ensure_ascii=False),
            created_by,
            ts,
            ts,
        ),
    )


def delete_analytics_scenario(con, scenario_id: int, scope: str = "observability") -> bool:
    cur = con.execute("DELETE FROM analytics_scenarios WHERE id=? AND scope=?", (int(scenario_id), scope))
    return int(cur.rowcount or 0) > 0


def set_analytics_scenario_favorite(con, scenario_id: int, user_id: int, favorite: bool) -> bool:
    if favorite:
        con.execute(
            """
            INSERT OR IGNORE INTO analytics_scenario_favorites(scenario_id, user_id, created_at_ms)
            VALUES(?,?,?)
            """,
            (int(scenario_id), int(user_id), now_ms()),
        )
        return True
    cur = con.execute(
        "DELETE FROM analytics_scenario_favorites WHERE scenario_id=? AND user_id=?",
        (int(scenario_id), int(user_id)),
    )
    return int(cur.rowcount or 0) > 0
