#!/usr/bin/env python3
"""
Shared constants for render scripts: Plotly JS (inline) and MW_LOGO_BASE64.

Plotly is embedded inline so dashboards work without internet access (plant
networks often block CDN traffic via firewall).  Resolution order:
  1. src/rendering/vendor/plotly-2.27.0.min.js  (pinned local copy)
  2. Python plotly package bundled JS                   (auto-installed)
  3. CDN <script src> fallback                          (requires internet)
"""
import os
import re

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.27.0.min.js"


def _load_plotly_js():
    vendor = os.path.join(os.path.dirname(__file__), "vendor", "plotly-2.27.0.min.js")
    if os.path.exists(vendor):
        with open(vendor, encoding="utf-8") as f:
            return f.read()
    try:
        import plotly as _plotly
        pkg_js = os.path.join(os.path.dirname(_plotly.__file__), "package_data", "plotly.min.js")
        if os.path.exists(pkg_js):
            with open(pkg_js, encoding="utf-8") as f:
                return f.read()
    except ImportError:
        pass
    return None  # caller will fall back to CDN <script src>


_PLOTLY_JS = _load_plotly_js()
if not _PLOTLY_JS:
    raise RuntimeError(
        "Plotly JS not found. Add vendor/plotly-2.27.0.min.js or install the plotly package. "
        "Do NOT fall back to CDN — plant networks block CDN traffic."
    )

PLOTLY_SCRIPT_TAG = f"<script>{_PLOTLY_JS}</script>"


_EXTERNAL_TAG_RE = re.compile(
    r'<(?:script|link|img)[^>]*(?:src|href)="https?://',
    re.IGNORECASE,
)


def _assert_self_contained(html: str, path: str) -> None:
    """Raise if any external URL is referenced from an HTML tag (script/link/img)."""
    bad = _EXTERNAL_TAG_RE.findall(html)
    if bad:
        raise RuntimeError(
            f"{path}: dashboard is NOT self-contained — external tag(s) found:\n"
            + "\n".join(f"  {b}..." for b in bad[:5])
        )

def build_html_head(title, extra_css=""):
    """Return the opening HTML boilerplate up to and including <body>.

    Produces:
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <meta name="viewport" ...>
          <title>{title}</title>
          <style>{CSS from theme.py}{extra_css}</style>
          {PLOTLY_SCRIPT_TAG}
        </head>
        <body>

    title: string used verbatim as the <title> content.
    extra_css: optional additional CSS rules appended after the canonical CSS.
               Use this for page-specific overrides not yet merged into theme.py.
    """
    # Import inside the function to avoid circular imports at module load time
    # (shared.py is imported by theme.py's dependents).
    from rendering.theme import CSS  # noqa: PLC0415
    combined_css = CSS + extra_css if extra_css else CSS
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
{combined_css}
</style>
{PLOTLY_SCRIPT_TAG}
</head>
<body>"""


def build_stop_summary(data):
    """Render the stop-summary KPI grid as an HTML string.

    data: dict with keys 'total_stops' (int), 'total_downtime_hours' (float),
          'longest_stop_min' (float).  Works for both per-shift and weekly data
          since both structures carry the same fields.
    """
    return f"""<div class="stop-summary-grid">
  <div class="stop-kpi">
    <div class="stop-kpi-label">Total Stops</div>
    <div class="stop-kpi-value">{data['total_stops']}</div>
  </div>
  <div class="stop-kpi">
    <div class="stop-kpi-label">Total Downtime</div>
    <div class="stop-kpi-value">{data['total_downtime_hours']:.1f}h</div>
  </div>
  <div class="stop-kpi">
    <div class="stop-kpi-label">Longest Stop</div>
    <div class="stop-kpi-value">{data['longest_stop_min']:.0f}m</div>
  </div>
</div>"""


def build_kpi_tiles(tiles):
    """Render a row of KPI tiles as an HTML string.

    tiles: list of dicts, each with keys:
        label  — display label (e.g. "OEE")
        val    — numeric value in percent (e.g. 82.5); formatted as "{val:.1f}%"
        color  — CSS color string for the value (e.g. from _kpi_color())
        delta  — HTML delta string (e.g. from _delta_html()), or empty string
        sub    — sub-label string shown below the value

    Returns an HTML string containing a <div class="kpi-grid"> with one
    <div class="kpi"> per tile.  Callers are responsible for computing each
    tile's val/color/delta/sub before calling this function.
    """
    html = '<div class="kpi-grid">\n'
    for t in tiles:
        html += f"""  <div class="kpi">
    <div class="kpi-label">{t['label']}</div>
    <div class="kpi-value" style="color:{t['color']};">{t['val']:.1f}%</div>
    {t['delta']}
    <div class="kpi-sub">{t['sub']}</div>
  </div>\n"""
    html += '</div>\n'
    return html


def build_warn_html(warnings: list) -> str:
    """Render a collapsible warnings block as an HTML string.

    Returns an empty string when warnings is empty.
    Callers should not guard with `if warnings` — this function handles that.
    """
    if not warnings:
        return ""
    items = "".join(f"<li>{w}</li>" for w in warnings)
    return f'<details><summary>{len(warnings)} warning(s)</summary><ul>{items}</ul></details>'


def build_bar_rows(rows: list, value_key: str, max_val: float, color: str, height: int = 12) -> str:
    """Build a sequence of horizontal bar-row HTML strings.

    rows      : list of dicts, each providing at least value_key and a 'label' key.
    value_key : key in each row dict for the numeric value.
    max_val   : value that maps to 100% bar width (use max of all values before calling).
    color     : CSS color for the bar fill.
    height    : bar height in pixels.

    Returns a single HTML string of concatenated row divs.
    """
    html = ""
    for row in rows:
        val = row[value_key]
        pct = min(100.0, (val / max_val * 100) if max_val else 0)
        html += (
            f'<div class="bar-row">'
            f'<span class="bar-label">{row["label"]}</span>'
            f'<div class="bar-track">'
            f'<div class="bar-fill" style="width:{pct:.1f}%;height:{height}px;background:{color};"></div>'
            f'</div>'
            f'<span class="bar-value">{val:.1f}</span>'
            f'</div>'
        )
    return html


def render_card_grid(cards: list) -> str:
    """Wrap a list of HTML card strings in a two-column grid.

    Pairs are placed side-by-side; an odd trailing card gets a filler div so the
    grid stays visually balanced.
    """
    html = '<div class="card-grid">'
    for i in range(0, len(cards), 2):
        pair = cards[i:i + 2]
        html += '<div class="card-row" style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">'
        html += pair[0]
        html += pair[1] if len(pair) > 1 else '<div></div>'
        html += '</div>'
    html += '</div>'
    return html


MW_LOGO_BASE64 = (
    "data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiPz4N"
    "CjxzdmcgaWQ9IkViZW5lXzEiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgeG1s"
    "bnM6eGxpbms9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkveGxpbmsiIHZlcnNpb249IjEuMSIgdmll"
    "d0JveD0iMCAwIDU1OS45IDI4MS45Ij4NCiAgPCEtLSBHZW5lcmF0b3I6IEFkb2JlIElsbHVzdHJh"
    "dG9yIDI5LjMuMSwgU1ZHIEV4cG9ydCBQbHVnLUluIC4gU1ZHIFZlcnNpb246IDIuMS4wIEJ1aWxk"
    "IDE1MSkgIC0tPg0KICA8ZGVmcz4NCiAgICA8c3R5bGU+DQogICAgICAuc3QwIHsNCiAgICAgICAg"
    "ZmlsbDogdXJsKCNVbmJlbmFubnRlcl9WZXJsYXVmKTsNCiAgICAgIH0NCg0KICAgICAgLnN0MSB7"
    "DQogICAgICAgIGZpbGw6ICNmZmY7DQogICAgICB9DQoNCiAgICAgIC5zdDIgew0KICAgICAgICBm"
    "aWxsOiAjZTYwMDdlOw0KICAgICAgfQ0KICAgIDwvc3R5bGU+DQogICAgPGxpbmVhckdyYWRpZW50"
    "IGlkPSJVbmJlbmFubnRlcl9WZXJsYXVmIiBkYXRhLW5hbWU9IlVuYmVuYW5udGVyIFZlcmxhdW"
    "YiIHgxPSIyNTEuOSIgeTE9IjIwNi40IiB4Mj0iNDIxLjMiIHkyPSIxNzIuNSIgZ3JhZGllbnRU"
    "cmFuc2Zvcm09InRyYW5zbGF0ZSgwIDI4Ni4xKSBzY2FsZSgxIC0xKSIgZ3JhZGllbnRVbml0cz0i"
    "dXNlclNwYWNlT25Vc2UiPg0KICAgICAgPHN0b3Agb2Zmc2V0PSIwIiBzdG9wLWNvbG9yPSIjMDA4"
    "Y2ZmIi8+DQogICAgICA8c3RvcCBvZmZzZXQ9Ii4yIiBzdG9wLWNvbG9yPSIjMDA4NmZmIi8+DQog"
    "ICAgICA8c3RvcCBvZmZzZXQ9Ii41IiBzdG9wLWNvbG9yPSIjMDA3OGZmIi8+DQogICAgICA8c3Rv"
    "cCBvZmZzZXQ9Ii43IiBzdG9wLWNvbG9yPSIjMDA2MGZmIi8+DQogICAgICA8c3RvcCBvZmZzZXQ9"
    "Ii44IiBzdG9wLWNvbG9yPSIjMDA1ZmZmIi8+DQogICAgICA8c3RvcCBvZmZzZXQ9Ii44IiBzdG9w"
    "LWNvbG9yPSIjMDA1NmZmIi8+DQogICAgICA8c3RvcCBvZmZzZXQ9Ii45IiBzdG9wLWNvbG9yPSIj"
    "MDAzZWZmIi8+DQogICAgICA8c3RvcCBvZmZzZXQ9Ii45IiBzdG9wLWNvbG9yPSIjMDAzMGZmIi8+"
    "DQogICAgPC9saW5lYXJHcmFkaWVudD4NCiAgPC9kZWZzPg0KICA8Zz4NCiAgICA8cGF0aCBjbGFz"
    "cz0ic3QyIiBkPSJNMjUxLjksNjIuNGgwYy4xLTIxLjMsMTAuOC00MCwyNy4xLTUxLjNDMjY4Ljgs"
    "NC4xLDI1Ni42LDAsMjQzLjMsMCwyMDguOCwwLDE4MC43LDI3LjksMTgwLjYsNjIuNGgwdjExMC43"
    "aDU0LjJWNjIuOGgwYzAtNC43LDMuOC04LjYsOC42LTguNnM4LjUsMy44LDguNSw4LjV2LS4zaDB6"
    "Ii8+DQogICAgPHBhdGggY2xhc3M9InN0MCIgZD0iTTM3Ny40LDYyLjRjLS4yLTM0LjUtMjguMi02"
    "Mi40LTYyLjgtNjIuNHMtNjIuNiwyNy45LTYyLjgsNjIuNGgwdjExMC43aDU0LjJWNjIuOGgwYzAt"
    "My4zLDEuOC02LjUsNC45LTcuOCw2LjItMi42LDEyLjIsMS45LDEyLjIsNy44aDB2MTEwLjRoNTQu"
    "MlY2Mi40aDBaIi8+DQogIDwvZz4NCiAgPGc+DQogICAgPHBhdGggY2xhc3M9InN0MSIgZD0iTTAs"
    "MjU3LjFjMC05LjgsNS43LTE0LjMsMTMuMi0xNC4zczguNiwyLDEwLjgsNS44YzIuMi0zLjgsNi01"
    "LjgsMTAuOC01LjgsNy41LDAsMTMuMiw0LjUsMTMuMiwxNC4zdjI0LjRoLTcuNHYtMjQuNGMwLTUu"
    "Mi0yLjgtNy40LTYuNC03LjRzLTYuNCwyLjItNi40LDcuNHYyNC40aC03LjR2LTI0LjRjMC01LjIt"
    "Mi44LTcuNC02LjQtNy40cy02LjQsMi4xLTYuNCw3LjR2MjQuNEgwdi0yNC40aDBaIi8+DQogICAg"
    "PHBhdGggY2xhc3M9InN0MSIgZD0iTTYzLjYsMjU5LjNjMC0xMC4xLDUuOC0xNi41LDE2LjUtMTYu"
    "NXMxNi42LDYuMywxNi42LDE2LjV2MjIuMmgtNy40di0xMS40aC0xOC41djExLjRoLTcuMnYtMjIu"
    "MlpNODkuNCwyNjMuNXYtNC4xYzAtNi4xLTMtOS42LTkuMy05LjZzLTkuMiwzLjUtOS4yLDkuNnY0"
    "LjFoMTguNVoiLz4NCiAgICA8cGF0aCBjbGFzcz0ic3QxIiBkPSJNMTEzLjIsMjQzLjFoNy41djM4"
    "LjVoLTcuNXYtMzguNVoiLz4NCiAgICA8cGF0aCBjbGFzcz0ic3QxIiBkPSJNMTM3LjUsMjQzLjFo"
    "MTguNGM3LjMsMCwxMS4xLDQuMiwxMS4xLDEwcy0xLjgsNi45LTUsOC41aDBjNC4yLDEuNSw2Ljgs"
    "NSw2LjgsOS42cy0zLjksMTAuNC0xMS40LDEwLjRoLTIwdi0zOC41Wk0xNTUsMjU4LjdjMy4xLDAs"
    "NC42LTEuOSw0LjYtNC4zcy0xLjItNC4zLTQuNi00LjNoLTkuOXY4LjZoOS45Wk0xNTYuMSwyNzQu"
    "NmMzLjUsMCw0LjktMi4xLDQuOS00LjZzLTEuOC00LjYtNC45LTQuNmgtMTEuMnY5LjJoMTEuMVoi"
    "Lz4NCiAgICA8cGF0aCBjbGFzcz0ic3QxIiBkPSJNMTgxLjIsMjYyLjNjMC0xMS4zLDguMy0xOS42"
    "LDIwLjEtMTkuNnMyMC4xLDguMywyMC4xLDE5LjYtOC4zLDE5LjYtMjAuMSwxOS42LTIwLjEtOC4z"
    "LTIwLjEtMTkuNmgwWk0yMTMuOCwyNjIuM2MwLTcuNC01LjEtMTIuOC0xMi42LTEyLjhzLTEyLjYs"
    "NS40LTEyLjYsMTIuOCw1LjEsMTIuOCwxMi42LDEyLjgsMTIuNi01LjQsMTIuNi0xMi44WiIvPg0K"
    "ICAgIDxwYXRoIGNsYXNzPSJzdDEiIGQ9Ik0yMzYuOSwyNDMuMWgxNi4xYzkuOCwwLDE0LjEsNS40"
    "LDE0LjEsMTIuN3MtMi45LDEwLjItOC4xLDEybDkuNSwxMy44aC04LjlsLTguMi0xM2gtNi45djEz"
    "aC03LjV2LTM4LjVoMFpNMjUzLjEsMjYxLjZjNC44LDAsNi40LTIuNSw2LjQtNS44cy0xLjYtNS43"
    "LTYuNC01LjdoLTguN3YxMS41aDguN1oiLz4NCiAgICA8cGF0aCBjbGFzcz0ic3QxIiBkPSJNMjgx"
    "LjcsMjU4LjdjMC05LjgsNS44LTE1LjgsMTYuNC0xNS44czE2LjQsNi4xLDE2LjQsMTUuOHYyMi45"
    "aC03LjV2LTIyLjljMC01LjYtMi45LTguOS04LjgtOC45cy04LjgsMy4zLTguOCw4Ljl2MjIuOWgt"
    "Ny41di0yMi45WiIvPg0KICAgIDxwYXRoIGNsYXNzPSJzdDEiIGQ9Ik0zMzAuMiwyNjcuNXYtMjQu"
    "NWg3LjR2MjQuNWMwLDUuMiwyLjgsNy40LDYuNSw3LjRzNi41LTIuMiw2LjUtNy40di0yNC41aDcu"
    "NHYyNC41YzAsNS4yLDIuOCw3LjQsNi41LDcuNHM2LjUtMi4xLDYuNS03LjR2LTI0LjVoNy40djI0"
    "LjVjMCw5LjgtNS44LDE0LjMtMTMuMiwxNC4zcy04LjYtMi0xMC44LTUuOGMtMi4yLDMuOC02LDUu"
    "OC0xMC44LDUuOC03LjUsMC0xMy4yLTQuNS0xMy4yLTE0LjNoMFoiLz4NCiAgICA8cGF0aCBjbGFz"
    "cz0ic3QxIiBkPSJNMzkxLjksMjYyLjNjMC0xMS4zLDguMy0xOS43LDIwLjEtMTkuN3MyMC4xLDgu"
    "MywyMC4xLDE5LjctOC4zLDE5LjctMjAuMSwxOS43LTIwLjEtOC4zLTIwLjEtMTkuN1pNNDI0Ljcs"
    "MjYyLjNjMC03LjQtNS4xLTEyLjgtMTIuNi0xMi44cy0xMi42LDUuNC0xMi42LDEyLjgsNS4xLDEy"
    "LjgsMTIuNiwxMi44LDEyLjYtNS40LDEyLjYtMTIuOFoiLz4NCiAgICA8cGF0aCBjbGFzcz0ic3Qx"
    "IiBkPSJNNDQ3LjcsMjQzLjFoNy41djMxLjVoMjAuMXY2LjloLTI3Ljd2LTM4LjVoMFoiLz4NCiAg"
    "ICA8cGF0aCBjbGFzcz0ic3QxIiBkPSJNNDg5LjYsMjQzLjFoMjcuOXY3aC0yMC40djguNWgxN3Y2"
    "LjhoLTE3djE2LjJoLTcuNXYtMzguNWgwWiIvPg0KICAgIDxwYXRoIGNsYXNzPSJzdDEiIGQ9Ik01"
    "MzIsMjQzLjFoMjcuOXY3aC0yMC40djguNWgxN3Y2LjhoLTE3djE2LjJoLTcuNXYtMzguNWgwWiIv"
    "Pg0KICA8L2c+DQo8L3N2Zz4="
)
