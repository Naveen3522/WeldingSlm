"""The web app's own files, and the only ones the server hands out.

    /                    pages/index.html                 /login, /agents, /library, ...   pages/<name>.html     (the address without .html is the page; with .html still works)
    /admin/users         pages/admin/users.html           /css/..., /js/...                the page styles and scripts, in their folders
    /vendor/material-symbols  the icon font (Google Material Symbols, cut down to the icons used), served from this computer (nothing is loaded from the internet)
    /favicon.svg

A path that matches none of these is not a file at all, so a request can never reach anything else on disk.
"""
import re
from pathlib import Path

TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".woff2": "font/woff2"}
PAGE = re.compile(r"^/(?:admin/)?[a-z]+(?:\.html)?$")
ASSET = re.compile(r"^/(?:(?:css|js)/(?:[a-z]+/)*[a-z0-9-]+\.(?:css|js)|vendor/material-symbols/[A-Za-z0-9.-]+\.woff2|favicon\.svg)$")


def static_file(web_dir, path):
    """The file behind a URL path inside `web_dir`, or None when the path is not one the web app serves (the file may still be missing: check .is_file())."""
    web_dir = Path(web_dir)
    if path == "/":
        path = "/index.html"
    if PAGE.match(path):
        return web_dir / "pages" / (path[1:] if path.endswith(".html") else path[1:] + ".html")
    if ASSET.match(path):
        return web_dir / path[1:]
    return None
