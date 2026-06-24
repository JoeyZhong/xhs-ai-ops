"""
Skills Hub API smoke tests (runs against running FastAPI server).
"""
import json, sys, uuid, urllib.request, subprocess

BASE = "http://localhost:8000/api/v1"
TOKEN = "dev_token_change_me"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

ok = True
test_count = 0

def req(method, path, body=None, expect=None):
    global ok, test_count
    test_count += 1
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        resp = urllib.request.urlopen(r)
        status = resp.status
        result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        status = e.code
        result = json.loads(e.read()) if e.reason else {"detail": str(e)}
    if expect is not None:
        passed = status == expect
    else:
        passed = status in (200, 201)
    if passed:
        print(f"  PASS {method} {path} -> {status}")
    else:
        print(f"  FAIL {method} {path} -> {status} (expected {expect or '200|201'}): {result}")
        ok = False
    return status, result


if __name__ == "__main__":
    print("=== Skills Hub Smoke Tests ===\n")

    # 0. Health
    print("0. Health check")
    req("GET", "/health")

    # 1. List universal skills (migrated)
    print("\n1. List universal skills")
    st, skills = req("GET", "/skills?owner=universal")
    assert st == 200 and len(skills) >= 6, f"Expected >=6 skills, got {len(skills)}"
    print(f"   Found {len(skills)} universal skills")

    # 2. Get a specific skill
    print("\n2. Get skill detail")
    sid = skills[0]["id"]
    st, detail = req("GET", f"/skills/{sid}")
    assert st == 200 and detail["body"] != ""
    print(f"   Skill: {detail['name']}, body={len(detail['body'])} chars")

    # 3. List all skills
    print("\n3. List all skills")
    st, all_skills = req("GET", "/skills?owner=all")
    assert st == 200 and len(all_skills) >= 6
    print(f"   Found {len(all_skills)} total skills")

    # 4. Create a private skill
    print("\n4. Create private skill")
    st, created = req("POST", "/skills", {
        "name": f"Test Skill {uuid.uuid4().hex[:4]}",
        "description": "A test skill created by smoke test",
        "body": "# Test\n\nThis is a test skill body.",
        "suggested_for": ["content"],
    })
    assert st == 201 and created["id"] and created["rev"] == 1
    print(f"   Created: {created['name']} (id={created['id'][:8]}...)")

    # 5. Create universal skill (should fail - not admin)
    print("\n5. Create universal skill (non-admin -> 403)")
    st, _ = req("POST", "/skills", {
        "name": "Should Fail",
        "description": "Trying to write universal pool",
        "body": "body",
        "owner": "universal",
    }, expect=403)
    assert st == 403
    print("   Correctly rejected")

    # 6. Update skill (OCC)
    print("\n6. Update skill (OCC)")
    st, updated = req("PUT", f"/skills/{created['id']}", {
        "name": f"{created['name']} (updated)",
        "expected_rev": 1,
    })
    assert st == 200 and updated["rev"] == 2
    print(f"   Updated: {updated['name']}, rev={updated['rev']}")

    # 7. OCC stale rev -> 409
    print("\n7. OCC stale rev -> 409")
    st, _ = req("PUT", f"/skills/{created['id']}", {
        "name": "Stale update",
        "expected_rev": 1,
    }, expect=409)
    assert st == 409
    print("   Correctly rejected")

    # 8. Fork
    print("\n8. Fork universal skill")
    st, forked = req("POST", f"/skills/{sid}/fork", {"name": "Forked Test Skill"})
    assert st == 201 and forked["source_skill_id"] == sid and forked["owner"] == "mine"
    print(f"   Forked: {forked['name']}, source={forked['source_skill_id'][:8]}..., owner={forked['owner']}")

    # 9. Equipment: list
    print("\n9. List equipment")
    st, equip = req("GET", "/agents/intel/equipment")
    assert st == 200
    print(f"   intel equipped: {len(equip)} skills")
    for s in equip:
        print(f"     - {s['name']}")

    # 10. Equip
    print("\n10. Equip")
    st, _ = req("POST", "/agents/intel/equipment", {"skill_id": forked["id"]}, expect=201)
    assert st == 201
    print(f"   Equipped")

    # 11. Unequip
    print("\n11. Unequip")
    st, _ = req("DELETE", f"/agents/intel/equipment/{forked['id']}")
    assert st == 200
    print(f"   Unequipped")

    # 12. Equip with non-visible skill -> 404
    print("\n12. Equip with non-visible skill -> 404")
    st, _ = req("POST", "/agents/intel/equipment",
        {"skill_id": "00000000-0000-0000-0000-000000000000"}, expect=404)
    assert st == 404
    print("   Correctly rejected")

    # 13. Delete skill -> cascade unequip
    print("\n13. Delete skill (cascade)")
    req("POST", "/agents/intel/equipment", {"skill_id": created["id"]}, expect=201)
    req("POST", "/agents/content/equipment", {"skill_id": created["id"]}, expect=201)
    st, result = req("DELETE", f"/skills/{created['id']}")
    assert st == 200 and len(result["unequipped_from"]) == 2
    print(f"   Deleted, unequipped_from: {result['unequipped_from']}")

    # 14. Get deleted skill -> 404
    print("\n14. Get deleted skill -> 404")
    st, _ = req("GET", f"/skills/{created['id']}", expect=404)
    assert st == 404
    print("   Correctly rejected")

    # 15. Migration idempotent (re-run)
    print("\n15. Migration idempotence")
    r = subprocess.run(
        [sys.executable, "scripts/migrate_skills_to_hub.py"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    stdout = (r.stdout or "") + (r.stderr or "")
    if "0 migrated" in stdout:
        print("   PASS: idempotent")
    else:
        print(f"   Unexpected: {stdout[:200]}")

    print(f"\n=== {'ALL PASSED' if ok else 'SOME FAILED'} ({test_count} tests) ===")
    sys.exit(0 if ok else 1)
