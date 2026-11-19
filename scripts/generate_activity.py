#!/usr/bin/env python3
"""Genera assets/activity.svg con el grafo de contribuciones de GitHub.

Datos:
  1. GitHub GraphQL API (requiere GITHUB_TOKEN o GH_TOKEN en el entorno).
  2. Fallback: scraping de la página pública de contribuciones (sin token).

Solo usa la librería estándar; no requiere pip install.
"""

import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

USER = os.environ.get("GH_USER", "ssant0")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(REPO_ROOT, "assets", "activity.svg")

API_URL = "https://api.github.com/graphql"
SCRAPE_URL = "https://github.com/users/{user}/contributions"

# Paleta (menos → más)
COLORS = ["#161B22", "#3B1D6E", "#6D28D9", "#8B5CF6", "#C4B5FD"]

# Geometría (780x190)
CELL = 10
GAP = 3
STEP = CELL + GAP  # 13
GRID_X = 44
GRID_Y = 62
ROWS = 7
BG = "#0D1117"
TEXT = "#8B949E"
TEXT_BRIGHT = "#F0F6FC"
ACCENT = "#A78BFA"
TITLE_FONT = "Segoe UI, Helvetica Neue, Arial, sans-serif"
MONO_FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

MESES = ["ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic"]


def level_for(count, max_count):
    """Convierte un conteo diario a nivel 0-4 (cuartiles, como GitHub)."""
    if count <= 0:
        return 0
    if max_count <= 4:
        return min(count, 4)
    for level, threshold in enumerate(
        (max_count * 0.25, max_count * 0.50, max_count * 0.75), start=1
    ):
        if count <= threshold:
            return level
    return 4


def fetch_graphql(user):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return None
    to = datetime.now(timezone.utc)
    frm = to - timedelta(days=365)
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    payload = json.dumps({
        "query": query,
        "variables": {
            "login": user,
            "from": frm.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "activity-graph-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if "errors" in data:
        raise RuntimeError(f"GraphQL: {data['errors']}")
    calendar = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    weeks = [
        [
            (date.fromisoformat(day["date"]), day["contributionCount"])
            for day in week["contributionDays"]
        ]
        for week in calendar["weeks"]
    ]
    return weeks, calendar["totalContributions"]


def fetch_scrape(user):
    """Fallback: parsear la página pública de contribuciones.

    Estructura actual del HTML: cada celda tiene data-date + id, y un
    <tool-tip for="{id}">N contributions on Month Dth.</tool-tip> con el conteo.
    """
    req = urllib.request.Request(
        SCRAPE_URL.format(user=user),
        headers={"User-Agent": "Mozilla/5.0 (activity-graph-generator)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8")

    cells = {
        day_id: date_str
        for date_str, day_id in re.findall(
            r'data-date="(\d{4}-\d{2}-\d{2})"[^>]*id="(contribution-day-component-[\d-]+)"',
            html,
        )
    }
    tooltips = dict(re.findall(
        r'for="(contribution-day-component-[\d-]+)"[^>]*>([^<]*)</tool-tip>',
        html,
    ))
    levels = dict(re.findall(
        r'id="(contribution-day-component-[\d-]+)"[^>]*data-level="(\d)"', html
    ))
    if not levels:
        levels = {
            day_id: level
            for level, day_id in re.findall(
                r'data-level="(\d)"[^>]*id="(contribution-day-component-[\d-]+)"', html
            )
        }

    if not cells:
        raise RuntimeError("No se encontraron datos de contribuciones en el HTML")

    days = []
    for day_id, date_str in cells.items():
        tooltip = tooltips.get(day_id, "")
        match = re.match(r"(?:(\d+)|No) contributions?", tooltip)
        count = int(match.group(1)) if match and match.group(1) else 0
        if not match and day_id in levels:
            # Sin tooltip utilizable: usar el nivel de GitHub como conteo aproximado
            count = int(levels[day_id])
        days.append((date.fromisoformat(date_str), count))
    days.sort(key=lambda item: item[0])
    # Agrupar en semanas (columnas) empezando en domingo
    weeks, current = [], []
    first_weekday = days[0][0].weekday()  # lunes=0 … domingo=6
    leading = (first_weekday + 1) % 7  # huecos antes del primer día (domingo=0)
    current = [None] * leading
    for day in days:
        current.append(day)
        if len(current) == ROWS:
            weeks.append(current)
            current = []
    if current:
        current += [None] * (ROWS - len(current))
        weeks.append(current)
    return weeks, sum(c for _, c in days)


def fetch_data(user):
    try:
        result = fetch_graphql(user)
        if result:
            return result, "graphql"
    except Exception as exc:  # noqa: BLE001
        print(f"Aviso: GraphQL falló ({exc}); intentando scraping…", file=sys.stderr)
    return fetch_scrape(user), "scrape"


def build_svg(weeks, total):
    n_cols = len(weeks)
    grid_w = n_cols * STEP - GAP
    max_count = max(
        (day[1] for week in weeks for day in week if day is not None), default=0
    )

    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="780" height="190" '
        f'viewBox="0 0 780 190" role="img" aria-labelledby="title desc">'
    )
    parts.append(f'  <title id="title">Actividad reciente de {USER}</title>')
    parts.append(
        f'  <desc id="desc">{total} contribuciones en los últimos 12 meses</desc>'
    )
    parts.append(f'  <rect width="780" height="190" fill="{BG}"/>')
    parts.append("")
    parts.append(
        f'  <g font-family="{TITLE_FONT}">'
    )
    parts.append(
        f'    <text x="24" y="30" font-size="14" font-weight="600" '
        f'fill="{TEXT_BRIGHT}">Actividad reciente</text>'
    )
    parts.append(
        f'    <text x="756" y="30" text-anchor="end" font-size="11" '
        f'fill="{ACCENT}">{total} contribuciones</text>'
    )
    parts.append("  </g>")
    parts.append("")

    # Etiquetas de meses (primera columna de cada mes, evitando el borde izquierdo)
    month_labels = []
    prev_month = None
    for col, week in enumerate(weeks):
        first_day = next((d for d in week if d is not None), None)
        if first_day is None:
            continue
        if first_day[0].month != prev_month:
            prev_month = first_day[0].month
            x = GRID_X + col * STEP
            if col >= 2 and x + 22 < GRID_X + grid_w:
                month_labels.append(f'<text x="{x}" y="50">{MESES[first_day[0].month - 1]}</text>')
    parts.append(
        f'  <g font-family="{MONO_FONT}" font-size="9" fill="{TEXT}">'
    )
    parts.append("    " + "".join(month_labels))
    day_labels = []
    for row, label in ((1, "lun"), (3, "mié"), (5, "vie")):
        y = GRID_Y + row * STEP + 9
        day_labels.append(f'<text x="34" y="{y}" text-anchor="end">{label}</text>')
    parts.append("    " + "".join(day_labels))
    parts.append("  </g>")
    parts.append("")

    # Celdas
    parts.append("  <g>")
    cells = []
    for col, week in enumerate(weeks):
        x = GRID_X + col * STEP
        for row in range(ROWS):
            y = GRID_Y + row * STEP
            day = week[row] if row < len(week) else None
            if day is None:
                color = COLORS[0]
            else:
                color = COLORS[level_for(day[1], max_count)]
            cells.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" fill="{color}"/>'
            )
    parts.append("    " + "".join(cells))
    parts.append("  </g>")
    parts.append("")

    # Leyenda
    parts.append(
        f'  <g font-family="{TITLE_FONT}" font-size="10" fill="{TEXT}">'
    )
    legend_x = GRID_X + grid_w - 121
    legend_y = GRID_Y + ROWS * STEP + 15
    parts.append(f'    <text x="{legend_x}" y="{legend_y + 9}">Menos</text>')
    squares = "".join(
        f'<rect x="{legend_x + 40 + i * STEP}" y="{legend_y}" width="{CELL}" '
        f'height="{CELL}" rx="2" fill="{color}"/>'
        for i, color in enumerate(COLORS)
    )
    parts.append("    " + squares)
    parts.append(f'    <text x="{legend_x + 40 + 5 * STEP + 9}" y="{legend_y + 9}">Más</text>')
    parts.append("  </g>")
    parts.append("")
    parts.append(
        f'  <rect x="{GRID_X - 10}" y="{GRID_Y - 8}" width="{grid_w + 20}" '
        f'height="{ROWS * STEP + 8}" fill="none" stroke="#C9D1D9" stroke-opacity="0.06" rx="6"/>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main():
    (weeks, total), source = fetch_data(USER)
    svg = build_svg(weeks, total)
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"OK ({source}): {total} contribuciones → {OUTPUT}")


if __name__ == "__main__":
    main()
