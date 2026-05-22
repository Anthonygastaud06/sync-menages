"""
cleanup_cloudinary.py
─────────────────────
Supprime de Cloudinary les photos de ménage plus anciennes que CLEANUP_DAYS
(par défaut 90 jours), pour rester dans l'offre gratuite (~25 Go).

Lancé une fois par jour par .github/workflows/cleanup.yml.
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CLOUD  = os.getenv("CLOUDINARY_CLOUD_NAME", "")
KEY    = os.getenv("CLOUDINARY_API_KEY", "")
SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
PREFIX = os.getenv("CLEANUP_PREFIX", "wac_menages")   # dossier des photos de ménage
DAYS   = int(os.getenv("CLEANUP_DAYS", "90"))         # âge max conservé
TIMEOUT = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

if not (CLOUD and KEY and SECRET):
    log.error("❌ Secrets Cloudinary manquants — nettoyage impossible")
    sys.exit(1)

session = requests.Session()
session.auth = (KEY, SECRET)
session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))

BASE   = f"https://api.cloudinary.com/v1_1/{CLOUD}"
cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS)


def list_old_public_ids():
    """Liste les public_id des images sous PREFIX/ créées avant le cutoff."""
    old, cursor, total = [], None, 0
    while True:
        params = {"prefix": PREFIX, "type": "upload", "max_results": 500}
        if cursor:
            params["next_cursor"] = cursor
        resp = session.get(f"{BASE}/resources/image", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for res in data.get("resources", []):
            total += 1
            created = res.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt < cutoff:
                old.append(res["public_id"])
        cursor = data.get("next_cursor")
        if not cursor:
            break
    log.info(f"📊 {total} image(s) sous '{PREFIX}/' — {len(old)} à supprimer (> {DAYS} j)")
    return old


def delete(public_ids):
    """Supprime les images par lots de 100."""
    deleted = 0
    for i in range(0, len(public_ids), 100):
        batch = public_ids[i:i + 100]
        resp = session.delete(
            f"{BASE}/resources/image/upload",
            params=[("public_ids[]", p) for p in batch],
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        deleted += len(resp.json().get("deleted", {}))
    return deleted


if __name__ == "__main__":
    old = list_old_public_ids()
    if not old:
        log.info("✓ Rien à supprimer")
    else:
        n = delete(old)
        log.info(f"🗑️  {n} image(s) supprimée(s)")
