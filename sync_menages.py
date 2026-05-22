"""
sync_menages.py
───────────────
Scrape White & Clean, détecte les missions terminées (fond vert)
et passe le statut de propreté (cleaningStatus) du listing Guesty à "clean".

Usage :
    python sync_menages.py              # une fois (exit 1 si erreur → alerte CI)
    python sync_menages.py --loop       # boucle toutes les 5 min (local)
"""

import os
import sys
import csv
import json
import time
import hashlib
import logging

import requests
import schedule
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from datetime import date
from pathlib import Path

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

WAC_EMAIL        = os.getenv("WAC_EMAIL", "ton_email@whiteandclean.fr")
WAC_PASSWORD     = os.getenv("WAC_PASSWORD", "ton_mot_de_passe")
WAC_LOGIN_URL    = "https://app.whiteandclean.fr/portal/customers/login"
WAC_MISSIONS_URL = "https://app.whiteandclean.fr/portal/customers/missions/reporting"

GUESTY_CLIENT_ID     = os.getenv("GUESTY_CLIENT_ID", "ton_client_id_guesty")
GUESTY_CLIENT_SECRET = os.getenv("GUESTY_CLIENT_SECRET", "ton_client_secret_guesty")
GUESTY_AUTH_URL      = "https://open-api.guesty.com/oauth2/token"
GUESTY_API_BASE      = "https://open-api.guesty.com/v1"

# Cloudinary : ré-hébergement permanent des photos de ménage.
# Si ces 3 variables sont absentes, la synchro des photos est simplement ignorée
# (la mise à jour du cleaningStatus continue de fonctionner).
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY    = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
SYNC_PHOTOS = bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)

# Fichier de mapping WAC ID → Guesty Listing ID (CSV : wac_id,guesty_id)
MAPPING_FILE = Path(__file__).parent / "mapping.csv"
# Cache du token Guesty — ⚠️ Guesty limite à 5 tokens / 24h / clientId.
# Le token (valable 24h) est donc persisté entre les runs (cf. cache GitHub Actions).
TOKEN_CACHE  = Path(__file__).parent / ".guesty_token.json"

# Délai max (s) avant d'abandonner une requête HTTP qui pend.
TIMEOUT = 30

# ─── LOGGING ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sync_menages.log"),
    ],
)
log = logging.getLogger(__name__)

# Token Guesty gardé en mémoire pour la durée du process
_token = None


def make_session():
    """Session HTTP avec retries auto sur erreurs transitoires (429 / 5xx)."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,                       # 0s, 2s, 4s entre les tentatives
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST", "PUT"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (sync-menages)"})
    return session


HTTP = make_session()  # session partagée pour Guesty

# ─── MAPPING ──────────────────────────────────────────────────────────────────

def load_mapping():
    """Lit mapping.csv → { wac_id: guesty_id }.
    Ignore l'en-tête, les lignes vides, les commentaires (#) et les
    appartements dont l'ID Guesty est vide."""
    mapping = {}
    with open(MAPPING_FILE, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row:
                continue
            wac = row[0].strip()
            # Saute commentaires, en-tête et lignes vides
            if not wac or wac.startswith("#") or wac.lower() in ("wac_id", "id wac"):
                continue
            guesty = row[1].strip() if len(row) > 1 else ""
            if not guesty:
                log.warning(f"   ⚠️  WAC {wac} sans ID Guesty dans le mapping — ignoré")
                continue
            mapping[wac] = guesty
    log.info(f"🗺️  {len(mapping)} appartement(s) dans le mapping")
    return mapping

# ─── GUESTY ───────────────────────────────────────────────────────────────────

def get_guesty_token(force=False):
    """Récupère un token Guesty Open API, en réutilisant le cache si possible.
    ⚠️ Guesty limite à 5 tokens / 24h / clientId : le cache est indispensable."""
    global _token

    if not force and _token:
        return _token

    if not force and TOKEN_CACHE.exists():
        try:
            data = json.loads(TOKEN_CACHE.read_text())
            if data.get("expires_at", 0) - time.time() > 600:  # marge 10 min
                log.info("✅ Token Guesty (cache réutilisé)")
                _token = data["access_token"]
                return _token
        except Exception:
            pass  # cache illisible → on en redemande un

    resp = HTTP.post(
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
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    _token = payload["access_token"]
    expires_in = payload.get("expires_in", 86400)
    try:
        TOKEN_CACHE.write_text(json.dumps({
            "access_token": _token,
            "expires_at": time.time() + expires_in,
        }))
    except Exception as e:
        log.warning(f"   ⚠️  Cache token non écrit : {e}")
    log.info("✅ Token Guesty obtenu (nouveau)")
    return _token


def guesty_request(method, path, **kwargs):
    """Appel Guesty authentifié, avec timeout et re-tentative unique si le
    token est rejeté (401) : on en regénère un et on rejoue la requête."""
    url = f"{GUESTY_API_BASE}{path}"
    base_headers = dict(kwargs.pop("headers", {}))
    base_headers.setdefault("Accept", "application/json")
    resp = None
    for attempt in (1, 2):
        token = get_guesty_token(force=(attempt == 2))
        headers = dict(base_headers)
        headers["Authorization"] = f"Bearer {token}"
        resp = HTTP.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
        if resp.status_code == 401 and attempt == 1:
            log.warning("   ⚠️  401 Guesty — token rafraîchi, nouvelle tentative")
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def get_cleaning_status(listing_id):
    """Retourne le cleaningStatus.value actuel du listing (clean/dirty/...)."""
    resp = guesty_request(
        "GET", f"/listings/{listing_id}", params={"fields": "cleaningStatus"}
    )
    return (resp.json().get("cleaningStatus") or {}).get("value")


def set_listing_clean(listing_id):
    """Passe le statut de propreté du listing à 'clean' (= 'Propre' dans Guesty)."""
    guesty_request(
        "PUT",
        f"/listings/{listing_id}",
        headers={"Content-Type": "application/json"},
        json={"cleaningStatus": {"value": "clean"}},
    )
    log.info(f"   🟢 Guesty listing {listing_id} → 'clean'")


def find_cleaning_task(listing_id, day):
    """Retrouve la tâche de ménage Guesty d'un listing pour une date donnée
    (jour ISO 'YYYY-MM-DD'). Renvoie la tâche (dict) ou None."""
    filters = json.dumps([
        {"field": "listingId",   "operator": "$eq",  "value": listing_id},
        {"field": "dateForSort", "operator": "$gte", "value": f"{day}T00:00:00.000Z"},
        {"field": "dateForSort", "operator": "$lte", "value": f"{day}T23:59:59.999Z"},
    ])
    resp = guesty_request("GET", "/tasks", params={"filters": filters, "limit": 25})
    results = resp.json().get("results", [])
    cleaning = [t for t in results if t.get("type") == "cleaning"]
    pool = cleaning or results
    return pool[0] if pool else None


def update_task(task_id, payload):
    """Met à jour une tâche Guesty (status / attachments / comments) en un seul PUT."""
    guesty_request(
        "PUT",
        f"/tasks/{task_id}",
        headers={"Content-Type": "application/json"},
        json=payload,
    )

# ─── CLOUDINARY ───────────────────────────────────────────────────────────────

def cloudinary_upload(image_url, public_id):
    """Envoie une image (par URL) sur Cloudinary et renvoie son URL permanente.
    Idempotent : même public_id → écrase, pas de doublon."""
    ts = str(int(time.time()))
    to_sign = f"overwrite=true&public_id={public_id}&timestamp={ts}"
    signature = hashlib.sha1((to_sign + CLOUDINARY_API_SECRET).encode()).hexdigest()
    resp = HTTP.post(
        f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload",
        data={
            "file": image_url,
            "api_key": CLOUDINARY_API_KEY,
            "timestamp": ts,
            "public_id": public_id,
            "overwrite": "true",
            "signature": signature,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["secure_url"]

# ─── WHITE & CLEAN ────────────────────────────────────────────────────────────

def login_wac():
    session = make_session()
    page = session.get(WAC_LOGIN_URL, timeout=TIMEOUT)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, "html.parser")
    csrf = soup.find("input", {"name": "_token"})
    payload = {"email": WAC_EMAIL, "password": WAC_PASSWORD}
    if csrf:
        payload["_token"] = csrf["value"]

    resp = session.post(WAC_LOGIN_URL, data=payload, allow_redirects=True, timeout=TIMEOUT)
    resp.raise_for_status()

    # Un HTTP 200 ne suffit pas : en cas de mauvais identifiants, White & Clean
    # réaffiche la page de login (toujours en 200). On vérifie donc l'URL finale.
    if "login" in resp.url.lower():
        raise Exception(
            f"❌ Connexion White & Clean refusée (identifiants ?) — redirigé vers {resp.url}"
        )
    log.info("✅ Connecté à White & Clean")
    return session


def get_completed_missions(session):
    """
    Retourne la liste des missions terminées (bg-mission-completed) :
    [{wac_id, mission_id, name}].
    """
    resp = session.get(WAC_MISSIONS_URL, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()

    # Si la session n'est pas (ou plus) authentifiée, WAC renvoie le login.
    if "login" in resp.url.lower():
        raise Exception("❌ Session White & Clean non authentifiée (page missions → login)")

    soup = BeautifulSoup(resp.text, "html.parser")
    completed = soup.find_all("div", class_="bg-mission-completed")
    log.info(f"🔍 {len(completed)} mission(s) terminée(s)")

    missions = []
    for mission in completed:
        apt_link = mission.find("a", href=lambda h: h and "/appartments/" in h)
        if not apt_link:
            continue
        wac_id = apt_link["href"].split("/appartments/")[-1].strip("/")
        span = apt_link.find("span")
        name = span.get_text(strip=True) if span else wac_id

        # Lien vers le détail de la mission (porte les photos) : …/missions/reporting/{id}
        det = mission.find("a", href=lambda h: h and "/missions/reporting/" in h)
        mission_id = det["href"].rstrip("/").split("/")[-1] if det else None

        log.info(f"   📍 {name} (WAC {wac_id}, mission {mission_id})")
        missions.append({"wac_id": wac_id, "mission_id": mission_id, "name": name})

    return missions


def get_mission_details(session, mission_id):
    """Récupère depuis la page détail d'une mission : ses photos + son commentaire.
    Photos WAC = publiques et permanentes (app.whiteandclean.fr/images/missions/…)."""
    resp = session.get(f"{WAC_MISSIONS_URL}/{mission_id}", timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    if "login" in resp.url.lower():
        raise Exception("❌ Session White & Clean non authentifiée (détail mission → login)")

    soup = BeautifulSoup(resp.text, "html.parser")

    # Photos (galeries baguetteBox)
    photos = []
    for a in soup.select("div.gallery a[href]"):
        href = a["href"].strip()
        if "/images/missions/" not in href:
            continue
        if href.startswith("/"):
            href = "https://app.whiteandclean.fr" + href
        if href not in photos:
            photos.append(href)

    # Commentaire : carte dont le titre <h5> est "Commentaire"
    comment = ""
    for h5 in soup.find_all("h5", class_="card-title"):
        if "commentaire" in h5.get_text(strip=True).lower():
            body = h5.find_parent("div", class_="card-body")
            if body:
                txt = body.get_text(" ", strip=True)
                # retire le mot-titre "Commentaire" en début
                if txt.lower().startswith("commentaire"):
                    txt = txt[len("commentaire"):].strip()
                comment = txt
            break

    return {"photos": photos, "comment": comment}

def sync_task(session, mission, listing_id):
    """Sur la tâche de ménage Guesty du jour : statut 'completed' + photos + commentaire
    de la mission WAC. Tout en un seul PUT, idempotent."""
    mission_id = mission.get("mission_id")
    if not mission_id:
        log.warning("   ⚠️  Pas d'ID de mission — tâche non synchronisée")
        return

    task = find_cleaning_task(listing_id, date.today().isoformat())
    if not task:
        log.warning(f"   ⚠️  Pas de tâche Guesty aujourd'hui pour {listing_id} — tâche/photos/commentaire non synchronisés")
        return

    details = get_mission_details(session, mission_id)
    payload = {}
    changes = []

    # 1) Statut → completed (Guesty passe alors le logement en 'Propre' automatiquement)
    if task.get("status") != "completed":
        payload["status"] = "completed"
        changes.append("statut→completed")

    # 2) Photos → Cloudinary puis attachées (dédup par nom de fichier)
    if SYNC_PHOTOS and details["photos"]:
        existing = [{"url": a.get("url"), "title": a.get("title"),
                     "mimetype": a.get("mimetype", "image/jpeg")}
                    for a in (task.get("attachments") or []) if a.get("url")]
        existing_titles = {a["title"] for a in existing}
        new_atts = []
        for url in details["photos"]:
            title = url.split("?")[0].rstrip("/").split("/")[-1]
            if title in existing_titles:
                continue
            public_id = f"wac_menages/{mission_id}/{title.rsplit('.', 1)[0]}"
            new_atts.append({"url": cloudinary_upload(url, public_id),
                             "title": title, "mimetype": "image/jpeg"})
        if new_atts:
            payload["attachments"] = existing + new_atts
            changes.append(f"{len(new_atts)} photo(s)")

    # 3) Commentaire WAC → commentaire de la tâche (préfixe [WAC], dédup par texte)
    if details["comment"]:
        marker = f"[WAC] {details['comment']}"
        existing_comments = task.get("comments") or []
        if not any((c.get("text") or "") == marker for c in existing_comments):
            kept = [{"text": c.get("text")} for c in existing_comments if c.get("text")]
            payload["comments"] = kept + [{"text": marker}]
            changes.append("commentaire")

    if not payload:
        log.info("   ✓ Tâche déjà à jour")
        return

    update_task(task["_id"], payload)
    log.info(f"   ✅ Tâche {task['_id']} : {', '.join(changes)}")

# ─── SYNC ─────────────────────────────────────────────────────────────────────

def sync():
    """Retourne True si tout s'est bien passé, False en cas d'erreur."""
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("🔄 Synchronisation...")
    if not SYNC_PHOTOS:
        log.info("ℹ️  Synchro photos désactivée (secrets Cloudinary absents)")

    # Étapes globales : toute erreur ici est fatale pour ce run.
    try:
        mapping = load_mapping()
        session = login_wac()
        missions = get_completed_missions(session)
    except Exception as e:
        log.error(f"❌ Erreur : {e}", exc_info=True)
        return False

    if not missions:
        log.info("✓ Aucune mission terminée")
        return True

    # Traitement par mission, isolé : un échec n'arrête pas les autres.
    errors = []
    for m in missions:
        wac_id = m["wac_id"]
        listing_id = mapping.get(wac_id)
        if not listing_id:
            log.warning(f"   ⚠️  WAC ID {wac_id} absent du mapping.csv — à ajouter")
            errors.append(wac_id)
            continue

        try:
            log.info(f"🏠 WAC:{wac_id} → Guesty:{listing_id}")
            # 1) Statut de propreté du logement (toujours — couvre le cas sans tâche)
            if get_cleaning_status(listing_id) == "clean":
                log.info("   ✓ Logement déjà 'clean'")
            else:
                set_listing_clean(listing_id)
            # 2) Tâche Guesty du jour : completed + photos + commentaire
            sync_task(session, m, listing_id)
        except Exception as e:
            log.error(f"   ❌ Échec WAC {wac_id} → listing {listing_id} : {e}")
            errors.append(wac_id)

    if errors:
        log.error(f"✗ Terminé avec {len(errors)} erreur(s) : {', '.join(errors)}")
        return False

    log.info("✓ Terminé sans erreur")
    return True

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
        # Code de sortie non nul si erreur → le job GitHub Actions devient rouge.
        sys.exit(0 if sync() else 1)
