#!/usr/bin/env python3
"""Validates aw-app.json against schemas/aw-app.schema.json, plus that every
referenced installer script and window spec actually exists on disk.
"""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent

manifest = json.loads((ROOT / "aw-app.json").read_text())
schema = json.loads((ROOT / "schemas" / "aw-app.schema.json").read_text())

jsonschema.validate(instance=manifest, schema=schema)

for cli in manifest["contributes"].get("system_clis", []):
    installer_path = ROOT / cli["installer"]
    if not installer_path.is_file():
        print(f"FAIL: installer script missing: {installer_path}", file=sys.stderr)
        sys.exit(1)

for win in manifest["contributes"].get("windows", []):
    body = win.get("body", {})
    if body.get("type") == "declarative":
        spec_path = ROOT / body["spec"]
        if not spec_path.is_file():
            print(f"FAIL: window spec missing: {spec_path}", file=sys.stderr)
            sys.exit(1)

print("OK: aw-app.json is valid, all installer scripts and window specs exist")
