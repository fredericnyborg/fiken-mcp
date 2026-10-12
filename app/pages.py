"""Felles HTML-mal for nettsidene (samtykke, /konto, feil) i Autoajour-stil.

Ren server-rendret HTML uten JS. All variabel tekst må escapes av kalleren.
"""

from __future__ import annotations

import html

CSS = """
:root{--bg:#f8fafc;--card:#fff;--border:#e2e8f0;--fg:#0f172a;--muted:#64748b;--soft:#f1f5f9;
--btn:#0f172a;--btn-fg:#fff;--btn-hover:#1e293b;--danger:#b91c1c;--logo-filter:none}
@media (prefers-color-scheme:dark){:root{--bg:#020617;--card:#0f172a;--border:#1e293b;--fg:#f1f5f9;
--muted:#94a3b8;--soft:#1e293b;--btn:#f1f5f9;--btn-fg:#0f172a;--btn-hover:#fff;--danger:#f87171;
--logo-filter:invert(1)}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:2rem 1rem;
background:var(--bg);color:var(--fg);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{width:100%;max-width:28rem}
.card{background:var(--card);border:1px solid var(--border);border-radius:1rem;padding:2rem;
box-shadow:0 10px 25px -5px rgb(15 23 42/.08),0 4px 10px -6px rgb(15 23 42/.08)}
.logo{display:block;width:150px;height:auto;margin:0 auto 1.5rem;filter:var(--logo-filter)}
h1{font-size:1.15rem;font-weight:600;text-align:center;margin:0 0 .5rem;letter-spacing:-.01em}
h2{font-size:1rem;font-weight:600;margin:1.75rem 0 .5rem}
p{margin:.5rem 0}.muted{color:var(--muted);font-size:.875rem}.center{text-align:center}
ul{margin:1rem 0;padding-left:1.25rem;font-size:.875rem}li{margin:.25rem 0}
.info{background:var(--soft);border-radius:.6rem;padding:.75rem;font-size:.8rem;color:var(--muted);margin-top:1rem}
.info b{color:var(--fg);font-weight:500}
.row{display:flex;gap:.75rem;margin-top:1.5rem}
.btn{flex:1;display:inline-flex;align-items:center;justify-content:center;gap:.4rem;border-radius:.6rem;
padding:.65rem 1rem;font-family:inherit;font-weight:500;font-size:.875rem;line-height:1.2;cursor:pointer;white-space:nowrap;text-decoration:none;border:1px solid transparent}
.btn-primary{background:var(--btn);color:var(--btn-fg)}.btn-primary:hover{background:var(--btn-hover)}
.btn-outline{background:transparent;color:var(--fg);border-color:var(--border)}.btn-outline:hover{background:var(--soft)}
.btn-danger{background:transparent;color:var(--danger);border-color:var(--border)}.btn-danger:hover{background:var(--soft)}
.btn-sm{flex:0 0 auto;padding:.4rem .75rem;font-size:.8rem}
.grant{border:1px solid var(--border);border-radius:.6rem;padding:.75rem;margin:.6rem 0;display:flex;
gap:.75rem;align-items:center;justify-content:space-between}.grant form{margin:0}
.foot{text-align:center;font-size:.75rem;color:var(--muted);margin-top:1rem}.foot a{color:inherit}
form{margin:0}
"""


def render(title: str, body_html: str) -> str:
    """Hel side: logo + kort med innhold. `body_html` er ferdig escapet HTML."""
    return (
        "<!doctype html><html lang=no><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)} – Autoajour</title>"
        "<link rel=icon href='/favicon.ico' sizes=any><link rel=icon type=image/png href='/icon.png'>"
        f"<style>{CSS}</style><body><main class=wrap><div class=card>"
        "<img class=logo src='/logo.png' alt='Autoajour'>"
        f"<h1>{html.escape(title)}</h1>{body_html}</div>"
        "<p class=foot>Fiken MCP fra Autoajour · <a href='/konto'>Mine tilkoblinger</a></p>"
        "</main></body></html>"
    )
