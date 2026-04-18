"""Preview-only driver. Local DB's ids differ from production (Pi) ids,
so we fake featured rows with recent local interactions and REMAP the real
overrides (from config/thinkatron_overrides.json) onto those local rows by
display position. Groups (temple, ballads) get real heads/stands/tags but
point at local ids we pick for them; solo overrides cycle through the rest."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path("/Users/swalsh/Desktop/canonbot")))
sys.path.insert(0, str(Path("/Users/swalsh/Desktop/canonbot/scripts")))

import json as _json
import build_thinkatron
from src.store import Store


def fake_fetch(store):
    rows = store._conn.execute(
        "SELECT * FROM interactions "
        "WHERE posts IS NOT NULL AND posts != '' AND posts != '[]' "
        "ORDER BY timestamp DESC LIMIT 18"
    ).fetchall()
    out = []
    for row in rows:
        ix = dict(row)
        for field in ("posts", "edited_posts", "passage_used", "passages_retrieved"):
            val = ix.get(field)
            if isinstance(val, str):
                try:
                    ix[field] = _json.loads(val)
                except Exception:
                    pass
        out.append(ix)
    return out


build_thinkatron._fetch_featured = fake_fetch


# --- Remap real overrides onto local rows -----------------------------------

OVERRIDES_PATH = Path("/Users/swalsh/Desktop/canonbot/config/thinkatron_overrides.json")
raw_overrides = _json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))

real_solo = [
    (k, v) for k, v in raw_overrides.items()
    if not k.startswith("_") and isinstance(v, dict)
]
real_groups = raw_overrides.get("_groups", {})


store = Store()
try:
    rows = fake_fetch(store)
    all_local_ids = [str(r["id"]) for r in rows]

    # Reserve local ids for the two real groups (temple: 2 parts, ballads: 3 parts).
    temple_ids = all_local_ids[:2]
    ballads_ids = all_local_ids[2:5]
    solo_ids = all_local_ids[5:]

    # Map each remaining local row to a real solo override by position (cycle if needed).
    solo_override_map = {}
    for i, lid in enumerate(solo_ids):
        if not real_solo:
            break
        _, ov = real_solo[i % len(real_solo)]
        solo_override_map[lid] = ov

    groups_map = {}
    if "temple" in real_groups and len(temple_ids) == 2:
        groups_map["temple"] = {**real_groups["temple"], "ids": temple_ids}
    if "ballads" in real_groups and len(ballads_ids) == 3:
        groups_map["ballads"] = {**real_groups["ballads"], "ids": ballads_ids}

    build_thinkatron._load_overrides = lambda: solo_override_map
    build_thinkatron._load_groups = lambda: groups_map

    build_dir = build_thinkatron.build(store)
    print(f"Preview at {build_dir}/index.html")
finally:
    store.close()
