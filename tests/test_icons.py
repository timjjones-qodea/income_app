from __future__ import annotations

import json
import struct
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


STATIC_DIR = Path(__file__).parents[1] / "app" / "static"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_header(path: Path) -> tuple[int, int, int, int]:
    data = path.read_bytes()
    assert data.startswith(PNG_SIGNATURE)
    length = struct.unpack(">I", data[8:12])[0]
    chunk_type = data[12:16]
    assert chunk_type == b"IHDR"
    assert length == 13
    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    return width, height, bit_depth, color_type


def test_icon_pngs_are_expected_sizes_and_opaque_rgb():
    expected = {
        "favicon-16x16.png": (16, 16),
        "favicon-32x32.png": (32, 32),
        "favicon-48x48.png": (48, 48),
        "apple-touch-icon-v2.png": (180, 180),
        "android-chrome-192x192.png": (192, 192),
        "android-chrome-512x512.png": (512, 512),
        "income-icon-master.png": (1254, 1254),
    }
    for filename, size in expected.items():
        width, height, bit_depth, color_type = png_header(STATIC_DIR / filename)
        assert (width, height) == size
        assert bit_depth == 8
        assert color_type == 2


def test_favicon_ico_contains_expected_sizes():
    data = (STATIC_DIR / "favicon-v2.ico").read_bytes()
    reserved, icon_type, count = struct.unpack("<HHH", data[:6])
    assert (reserved, icon_type, count) == (0, 1, 3)

    sizes = set()
    for index in range(count):
        entry = data[6 + index * 16 : 22 + index * 16]
        width, height, *_ = struct.unpack("<BBBBHHII", entry)
        sizes.add((256 if width == 0 else width, 256 if height == 0 else height))

    assert sizes == {(16, 16), (32, 32), (48, 48)}


def test_icon_routes_support_get_and_head():
    client = TestClient(app)
    routes = {
        "/favicon.ico": "image/x-icon",
        "/favicon-16x16.png": "image/png",
        "/favicon-32x32.png": "image/png",
        "/apple-touch-icon.png": "image/png",
        "/apple-touch-icon-v2.png": "image/png",
        "/android-chrome-192x192.png": "image/png",
        "/android-chrome-512x512.png": "image/png",
        "/site.webmanifest": "application/manifest+json",
    }

    for path, media_type in routes.items():
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(media_type)
        assert response.content

        head_response = client.head(path)
        assert head_response.status_code == 200
        assert head_response.headers["content-type"].startswith(media_type)


def test_web_manifest_references_root_icon_routes():
    manifest = json.loads((STATIC_DIR / "site.webmanifest").read_text())
    assert manifest["name"] == "Retirement Income"
    assert manifest["short_name"] == "Income"
    assert {icon["src"] for icon in manifest["icons"]} == {
        "/android-chrome-192x192.png",
        "/android-chrome-512x512.png",
        "/apple-touch-icon-v2.png",
    }
