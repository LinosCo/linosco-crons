#!/usr/bin/env python3
"""
Monitor giornaliero per contradatezzamicheloni.it
Controlla sito web e email, invia report su Telegram.
"""

import os
import sys
import ssl
import socket
import smtplib
import datetime
import urllib.request
import urllib.error
import json
import subprocess

DOMAIN = "contradatezzamicheloni.it"
SITE_URL = f"https://www.{DOMAIN}"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

GA4_PROPERTY_ID = "528461351"
SC_SITE = "sc-domain:contradatezzamicheloni.it"
GCLOUD_PROJECT = "volerai"

MX_HOSTS = [
    "mx10.antispam.mailspamprotection.com",
    "mx20.antispam.mailspamprotection.com",
    "mx30.antispam.mailspamprotection.com",
]


def check_website():
    """Controlla che il sito risponda con HTTP 200."""
    try:
        start = datetime.datetime.now()
        req = urllib.request.Request(SITE_URL, method="GET")
        req.add_header("User-Agent", "ContradaMonitor/1.0")
        with urllib.request.urlopen(req, timeout=15) as resp:
            elapsed = (datetime.datetime.now() - start).total_seconds()
            status = resp.status
            ok = 200 <= status < 400
            return ok, f"HTTP {status} in {elapsed:.2f}s"
    except Exception as e:
        return False, f"Errore: {e}"


def check_ssl():
    """Controlla validita certificato SSL."""
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=f"www.{DOMAIN}") as s:
            s.settimeout(10)
            s.connect((f"www.{DOMAIN}", 443))
            cert = s.getpeercert()
            not_after = datetime.datetime.strptime(
                cert["notAfter"], "%b %d %H:%M:%S %Y %Z"
            )
            days_left = (not_after - datetime.datetime.now(datetime.UTC).replace(tzinfo=None)).days
            if days_left < 7:
                return False, f"Scade tra {days_left} giorni!"
            return True, f"Valido, scade tra {days_left} giorni"
    except Exception as e:
        return False, f"Errore: {e}"


def check_dns():
    """Controlla risoluzione DNS del dominio."""
    try:
        # Prova root domain, fallback su www
        try:
            ip = socket.gethostbyname(DOMAIN)
        except socket.gaierror:
            ip = socket.gethostbyname(f"www.{DOMAIN}")
        # Vercel usa vari IP, basta che risolva
        if ip:
            return True, f"OK ({ip})"
        return False, "Nessuna risoluzione"
    except Exception as e:
        return False, f"Errore: {e}"


def check_mx():
    """Controlla che almeno un server MX sia raggiungibile sulla porta 25."""
    reachable = []
    unreachable = []
    for mx in MX_HOSTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect((mx, 25))
            s.close()
            reachable.append(mx.split(".")[0])
        except Exception:
            unreachable.append(mx.split(".")[0])

    if not reachable:
        return False, "Nessun MX raggiungibile!"
    if unreachable:
        return True, f"OK ({', '.join(reachable)}), down: {', '.join(unreachable)}"
    return True, f"Tutti OK ({', '.join(reachable)})"


def check_smtp_starttls():
    """Controlla che il server MX supporti STARTTLS."""
    try:
        with smtplib.SMTP(MX_HOSTS[0], 25, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            return True, "STARTTLS supportato"
    except Exception as e:
        return False, f"Errore: {e}"


def get_gcloud_token():
    """Ottiene access token gcloud per API Google."""
    try:
        result = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:
        return None


def api_request(url, body, token):
    """Esegue POST autenticato verso API Google."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-goog-user-project", GCLOUD_PROJECT)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_ga4(token):
    """Report GA4: sessioni, utenti, pageviews ultimi 7 giorni vs 7 precedenti."""
    if not token:
        return False, "Token gcloud non disponibile"
    try:
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY_ID}:runReport"
        body = {
            "dateRanges": [
                {"startDate": "7daysAgo", "endDate": "today"},
                {"startDate": "14daysAgo", "endDate": "8daysAgo"},
            ],
            "metrics": [
                {"name": "sessions"},
                {"name": "totalUsers"},
                {"name": "screenPageViews"},
            ],
        }
        data = api_request(url, body, token)
        rows = data.get("rows", [])
        if not rows:
            return False, "Nessun dato GA4"

        # dateRange 0 = ultimi 7gg, dateRange 1 = 7gg precedenti
        current = next((r for r in rows if r["dimensionValues"][0]["value"] == "date_range_0"), None)
        previous = next((r for r in rows if r["dimensionValues"][0]["value"] == "date_range_1"), None)

        if not current:
            return False, "Nessun dato periodo corrente"

        sessions = int(current["metricValues"][0]["value"])
        users = int(current["metricValues"][1]["value"])
        pageviews = int(current["metricValues"][2]["value"])

        parts = [f"Sessioni: {sessions}", f"Utenti: {users}", f"Pageviews: {pageviews}"]

        if previous:
            prev_sessions = int(previous["metricValues"][0]["value"])
            prev_users = int(previous["metricValues"][1]["value"])
            if prev_sessions > 0:
                delta_s = ((sessions - prev_sessions) / prev_sessions) * 100
                parts[0] += f" ({delta_s:+.0f}%)"
            if prev_users > 0:
                delta_u = ((users - prev_users) / prev_users) * 100
                parts[1] += f" ({delta_u:+.0f}%)"

        return True, " | ".join(parts)
    except Exception as e:
        return False, f"Errore: {e}"


def check_search_console(token):
    """Report Search Console: click, impression, CTR, posizione + top query."""
    if not token:
        return False, "Token gcloud non disponibile"
    try:
        sc_site_encoded = "sc-domain%3Acontradatezzamicheloni.it"
        url = f"https://www.googleapis.com/webmasters/v3/sites/{sc_site_encoded}/searchAnalytics/query"

        today = datetime.date.today()
        # SC ha dati con 2-3 giorni di ritardo
        end_date = (today - datetime.timedelta(days=3)).isoformat()
        start_date = (today - datetime.timedelta(days=10)).isoformat()

        # Metriche generali
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": [],
        }
        data = api_request(url, body, token)
        rows = data.get("rows", [])
        if not rows:
            return False, "Nessun dato Search Console"

        row = rows[0]
        clicks = int(row["clicks"])
        impressions = int(row["impressions"])
        ctr = row["ctr"] * 100
        position = row["position"]

        summary = f"Click: {clicks} | Impression: {impressions} | CTR: {ctr:.1f}% | Pos. media: {position:.1f}"

        # Top 5 query
        body_q = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["query"],
            "rowLimit": 5,
        }
        data_q = api_request(url, body_q, token)
        top_queries = data_q.get("rows", [])
        if top_queries:
            q_list = []
            for i, q in enumerate(top_queries, 1):
                query = q["keys"][0]
                qc = int(q["clicks"])
                qi = int(q["impressions"])
                q_list.append(f"  {i}. _{query}_ ({qc} click, {qi} imp)")
            summary += "\nTop query:\n" + "\n".join(q_list)

        return True, summary
    except Exception as e:
        return False, f"Errore: {e}"


def send_telegram(message):
    """Invia messaggio su Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status == 200


def main():
    checks = [
        ("Sito web", check_website),
        ("Certificato SSL", check_ssl),
        ("DNS", check_dns),
        ("Server MX", check_mx),
        ("SMTP/TLS", check_smtp_starttls),
    ]

    results = []
    all_ok = True
    for name, fn in checks:
        ok, detail = fn()
        if not ok:
            all_ok = False
        icon = "\u2705" if ok else "\u274c"
        results.append(f"{icon} *{name}*: {detail}")

    # SEO checks (GA4 + Search Console)
    token = get_gcloud_token()
    seo_results = []

    seo_checks = [
        ("GA4 (7 giorni)", check_ga4),
        ("Search Console (7 giorni)", check_search_console),
    ]
    for name, fn in seo_checks:
        ok, detail = fn(token)
        icon = "\u2705" if ok else "\u26a0\ufe0f"
        seo_results.append(f"{icon} *{name}*\n{detail}")

    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    status = "\u2705 Tutto OK" if all_ok else "\u26a0\ufe0f PROBLEMI RILEVATI"

    message = (
        f"\U0001f3e0 *Monitor {DOMAIN}*\n"
        f"\U0001f4c5 {now}\n"
        f"\n{status}\n\n"
        + "\n".join(results)
        + "\n\n\U0001f4c8 *Report SEO*\n\n"
        + "\n\n".join(seo_results)
    )

    print(message)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            send_telegram(message)
            print("\nReport inviato su Telegram.")
        except Exception as e:
            print(f"\nErrore invio Telegram: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("\nTELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non impostati, skip Telegram.")

    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
