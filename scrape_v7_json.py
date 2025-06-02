"""Fetch the embedded OpenAPI spec (oasDefinition) from V7 Labs docs.

No headless browser is required: every endpoint page embeds the full spec in
<scr ipt id="ssr-props" data-initial-props="…">. We just need to fetch one
endpoint page and read that blob.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup
from openapi_spec_validator import validate_spec

BASE = "https://docs.go.v7labs.com/reference"
OUTPUT = Path(__file__).with_name("swagger.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0"
    )
}


def _get_initial_props(url: str) -> Dict[str, Any]:
    """Return the JSON object stored in the `data-initial-props` attribute."""
    resp = requests.get(url, headers=HEADERS, timeout=(5, 15))
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    tag = soup.select_one("#ssr-props")
    if not tag:
        return {}
    raw = html.unescape(tag["data-initial-props"])
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        snippet = raw[:120] + ("…" if len(raw) > 120 else "")
        print(f"    [warn] JSON decode error on {url}: {exc} | snippet: {snippet}")
        return {}


def get_endpoint_slugs() -> List[str]:
    """Return all endpoint slugs from the reference sidebar (refs)."""
    data = _get_initial_props(BASE)
    sidebars_root = data.get("sidebars", {})

    # Gracefully handle missing sidebar data.
    try:
        sidebars = sidebars_root.get("refs") or next(iter(sidebars_root.values()))
    except (AttributeError, StopIteration):
        raise RuntimeError("Could not locate 'refs' sidebar in initial props; site layout may have changed")

    # Sometimes `sidebars` is a dict, sometimes a list.
    if isinstance(sidebars, dict):
        sidebar_nodes = [sidebars]
    else:
        sidebar_nodes = sidebars or []

    def walk(nodes):
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if n.get("type") == "endpoint" and n.get("slug"):
                yield n["slug"]
            # Children can be under several keys depending on Stoplight version.
            children = []
            for key in ("pages", "children", "items"):
                children.extend(n.get(key, []))
            if children:
                yield from walk(children)

    return list(dict.fromkeys(walk(sidebar_nodes)))  # de-dupe, keep order


def _find_oas(obj: Any):
    """Recursively search for a dict that looks like an OpenAPI spec."""
    if isinstance(obj, dict):
        if obj.get("paths") and obj.get("openapi"):
            # Looks like a full spec.
            return obj
        for v in obj.values():
            found = _find_oas(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_oas(item)
            if found:
                return found
    return None


def fetch_oas_definition(slugs: List[str]):
    """Iterate endpoint slugs until we find an oasDefinition blob."""
    for slug in slugs:
        url = f"{BASE}/{slug}"
        print(f"[TRY] {url}")
        try:
            props = _get_initial_props(url)
        except Exception as exc:
            print(f"    [skip] error fetching {slug}: {exc}")
            continue

        spec = props.get("oasDefinition") or _find_oas(props)
        if spec and spec.get("paths"):
            print(f"[FOUND] Spec with {len(spec['paths'])} paths in {slug}")
            return spec

    raise RuntimeError("Failed to find oasDefinition in any endpoint page")


def main():
    slugs = get_endpoint_slugs()
    print(f"Discovered {len(slugs)} endpoint pages")
    spec = fetch_oas_definition(slugs)

    # Remove illegal top-level fields that aren't OpenAPI extensions.
    for key in list(spec.keys()):
        if key.startswith("_") and not key.startswith("x-"):
            spec.pop(key, None)

    # Validate spec (raises if invalid)
    try:
        validate_spec(spec)
    except Exception as exc:  # broad but validator may raise various subclasses
        print(f"[warn] Spec validation failed: {exc}")

    OUTPUT.write_text(json.dumps(spec, indent=2, ensure_ascii=False))
    print(f"Swagger spec written to {OUTPUT.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("[!] Error:", exc, file=sys.stderr)
        sys.exit(1) 