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
WAC_LOGIN_URL    = "https://app.whiteandclean.fr/portal/customers/login"
WAC_MISSIONS_URL = "https://app.whiteandclean.fr/portal/customers/missions/reporting"

GUESTY_CLIENT_ID     = os.getenv("GUESTY_CLIENT_ID", "ton_client_id_guesty")
GUESTY_CLIENT_SECRET = os.getenv("GUESTY_CLIENT_SECRET", "ton_client_secret_guesty")
GUESTY_AUTH_URL      = "https://open-api.guesty.com/oauth2/token"
GUESTY_API_BASE      = "https://open-api.guesty.com/v1"

# Fichier de mapping WAC ID → Guesty Listing ID
MAPPING_FILE = Path(__file__).parent / "mapping.json"
# Cache du token Guesty — ⚠️ Guesty limite à 5 tokens / 24h / clientId.
# Le token (valable 24h) est donc persisté entre les runs (cf. cache GitHub Actions).
TOKEN_CACHE  = Path(__file__).parent / ".guesty_token.json"

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
    """Récupère un token Guesty Open API, en réutilisant le cache si possible.
    ⚠️ Guesty limite à 5 tokens / 24h / clientId : le cache est indispensable."""
    if TOKEN_CACHE.exists():
        try:
            data = json.loads(TOKEN_CACHE.read_text())
            if data.get("expires_at", 0) - time.time() > 600:  # marge 10 min
                log.info("✅ Token Guesty (cache réutilisé)")
                return data["access_token"]
        except Exception:
            pass  # cache illisible → on en redemande un

    resp = requests.post(
        GUESTY_AUTH_URL,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "open-api",
            "client_id": GUESTY_CLIENT_ID,
            "client_secret": GUESTY_CLIENT_SECRET,
        },
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload["access_token"]
    expires_in = payload.get("expires_in", 86400)
    try:
        TOKEN_CACHE.write_text(json.dumps({
            "access_token": token,
            "expires_at": time.time() + expires_in,
        }))
    except Exception as e:
        log.warning(f"   ⚠️  Cache token non écrit : {e}")
    log.info("✅ Token Guesty obtenu (nouveau)")
    return token


def get_cleaning_status(token, listing_id):
    """Retourne le cleaningStatus.value actuel du listing (clean/dirty/...)."""
    resp = requests.get(
        f"{GUESTY_API_BASE}/listings/{listing_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={"fields": "cleaningStatus"},
    )
    resp.raise_for_status()
    return (resp.json().get("cleaningStatus") or {}).get("value")


def set_listing_clean(token, listing_id):
    """Passe le statut de propreté du listing à 'clean' (= 'Propre' dans Guesty)."""
    resp = requests.put(
        f"{GUESTY_API_BASE}/listings/{listing_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={"cleaningStatus": {"value": "clean"}},
    )
    resp.raise_for_status()
    log.info(f"   🟢 Guesty listing {listing_id} → 'clean'")

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

            current = get_cleaning_status(token, listing_id)
            if current == "clean":
                log.info(f"   ✓ Déjà 'clean' — rien à faire")
                already_synced.add(cache_key)
                continue

            set_listing_clean(token, listing_id)
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
