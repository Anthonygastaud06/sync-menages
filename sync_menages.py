"""
sync_menages.py
───────────────
Scrape White & Clean, détecte les missions terminées (fond vert)
et met à jour le statut housekeeping dans Guesty.

Usage :
    python sync_menages.py              # une fois
    python sync_menages.py --loop       # boucle toutes les 5 min
"""

import requests
import json
import logging
import sys
import time
import schedule
from bs4 import BeautifulSoup
from datetime import date
from pathlib import Path

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

import os
WAC_EMAIL        = os.getenv("WAC_EMAIL", "ton_email@whiteandclean.fr")
WAC_PASSWORD     = os.getenv("WAC_PASSWORD", "ton_mot_de_passe")
WAC_LOGIN_URL    = "https://app.whiteandclean.fr/portal/login"
WAC_MISSIONS_URL = "https://app.whiteandclean.fr/portal/customers/missions/reporting"

GUESTY_CLIENT_ID     = os.getenv("GUESTY_CLIENT_ID", "ton_client_id_guesty")
GUESTY_CLIENT_SECRET = os.getenv("GUESTY_CLIENT_SECRET", "ton_client_secret_guesty")
GUESTY_AUTH_URL      = "https://auth.guesty.com/oauth/token"
GUESTY_API_BASE      = "https://open-api.guesty.com/v1"

# Fichier de mapping WAC ID → Guesty Listing ID
MAPPING_FILE = Path(__file__).parent / "mapping.json"

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sync_menages.log")
    ]
)
log = logging.getLogger(__name__)

# Cache journalier (évite les doublons)
already_synced = set()

# ─── MAPPING ──────────────────────────────────────────────────────────────────

def load_mapping():
    with open(MAPPING_FILE) as f:
        data = json.load(f)
    # Ignore les clés _comment
    return {k: v for k, v in data.items() if not k.startswith("_")}

# ─── GUESTY ───────────────────────────────────────────────────────────────────

def get_guesty_token():
    resp = requests.post(GUESTY_AUTH_URL, json={
        "grant_type": "client_credentials",
        "client_id": GUESTY_CLIENT_ID,
        "client_secret": GUESTY_CLIENT_SECRET,
    })
    resp.raise_for_status()
    log.info("✅ Token Guesty obtenu")
    return resp.json()["access_token"]


def get_todays_reservation(token, listing_id):
    today = date.today().isoformat()
    resp = requests.get(
        f"{GUESTY_API_BASE}/reservations",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "listingId": listing_id,
            "checkOutFrom": today,
            "checkOutTo": today,
            "status": "confirmed",
            "limit": 1,
        }
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0]["_id"] if results else None


def set_clean(token, reservation_id):
    resp = requests.put(
        f"{GUESTY_API_BASE}/reservations/{reservation_id}/housekeeping",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"status": "clean"}
    )
    resp.raise_for_status()
    log.info(f"   🟢 Guesty → 'clean' (réservation {reservation_id})")

# ─── WHITE & CLEAN ────────────────────────────────────────────────────────────

def login_wac():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    page = session.get(WAC_LOGIN_URL)
    soup = BeautifulSoup(page.text, "html.parser")
    csrf = soup.find("input", {"name": "_token"})
    payload = {"email": WAC_EMAIL, "password": WAC_PASSWORD}
    if csrf:
        payload["_token"] = csrf["value"]
    resp = session.post(WAC_LOGIN_URL, data=payload, allow_redirects=True)
    if resp.status_code == 200:
        log.info("✅ Connecté à White & Clean")
        return session
    raise Exception(f"❌ Échec connexion White & Clean ({resp.status_code})")


def get_completed_wac_ids(session):
    """
    Retourne la liste des IDs d'appartements WAC
    dont la mission est terminée (bg-mission-completed).
    """
    resp = session.get(WAC_MISSIONS_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    completed = soup.find_all("div", class_="bg-mission-completed")
    log.info(f"🔍 {len(completed)} mission(s) terminée(s)")

    wac_ids = []
    for mission in completed:
        apt_link = mission.find("a", href=lambda h: h and "/appartments/" in h)
        if apt_link:
            wac_id = apt_link["href"].split("/appartments/")[-1].strip("/")
            # Récupère aussi le nom pour le log
            span = apt_link.find("span")
            name = span.get_text(strip=True) if span else wac_id
            log.info(f"   📍 {name} (WAC ID: {wac_id})")
            wac_ids.append(wac_id)

    return wac_ids

# ─── SYNC ─────────────────────────────────────────────────────────────────────

def sync():
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("🔄 Synchronisation...")

    try:
        mapping = load_mapping()
        session = login_wac()
        wac_ids = get_completed_wac_ids(session)

        if not wac_ids:
            log.info("✓ Aucune mission terminée")
            return

        token = get_guesty_token()

        for wac_id in wac_ids:
            cache_key = f"{date.today().isoformat()}_{wac_id}"
            if cache_key in already_synced:
                log.info(f"   ⏭️  WAC {wac_id} déjà synchronisé aujourd'hui")
                continue

            listing_id = mapping.get(wac_id)
            if not listing_id:
                log.warning(f"   ⚠️  WAC ID {wac_id} absent du mapping.json — à ajouter")
                continue

            log.info(f"🏠 WAC:{wac_id} → Guesty:{listing_id}")

            reservation_id = get_todays_reservation(token, listing_id)
            if not reservation_id:
                log.warning(f"   ⚠️  Pas de réservation checkout aujourd'hui")
                continue

            set_clean(token, reservation_id)
            already_synced.add(cache_key)

    except Exception as e:
        log.error(f"❌ Erreur : {e}", exc_info=True)

    log.info("✓ Terminé")

# ─── LANCEMENT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--loop" in sys.argv:
        log.info("🔁 Boucle toutes les 5 minutes (Ctrl+C pour arrêter)")
        sync()
        schedule.every(5).minutes.do(sync)
        while True:
            schedule.run_pending()
            time.sleep(1)
    else:
        sync()
