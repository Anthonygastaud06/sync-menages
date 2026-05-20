# Projet sync-menages

## Objectif
Script Python qui tourne toutes les 5 min via GitHub Actions.
Il scrape White & Clean, détecte les ménages terminés et met à jour Guesty automatiquement.

## Fonctionnement
1. Connexion à `app.whiteandclean.fr` (email/password)
2. Scrape `https://app.whiteandclean.fr/portal/customers/missions/reporting`
3. Détecte les missions terminées → classe CSS `bg-mission-completed`
4. Récupère l'ID appartement WAC depuis l'URL `/appartments/XXXX`
5. Mappe avec l'ID listing Guesty via `mapping.json`
6. Cherche la réservation checkout du jour dans Guesty
7. Met à jour housekeeping → `clean` via API Guesty

## Structure HTML White & Clean
```html
<div class="row pb-2 mx-0 bg-mission-completed">
  <a href="/portal/customers/appartments/2329">
    <span>12 BOULEVARD JEAN JAURÈS 06300 NICE</span>
  </a>
</div>
```

## Fichiers
| Fichier | Rôle |
|---|---|
| `sync_menages.py` | Script principal |
| `mapping.json` | WAC ID → Guesty Listing ID (à compléter) |
| `.github/workflows/sync.yml` | GitHub Actions toutes les 5 min |

## Ce qu'il faut faire
1. Vérifier que Git et la CLI GitHub (`gh`) sont installés
2. Créer un repo GitHub privé `sync-menages`
3. Pusher les 3 fichiers
4. Configurer les 4 secrets GitHub Actions :
   - `WAC_EMAIL`
   - `WAC_PASSWORD`
   - `GUESTY_CLIENT_ID`
   - `GUESTY_CLIENT_SECRET`
5. Vérifier que le premier workflow se lance

## API Guesty
- Auth : `POST https://auth.guesty.com/oauth/token`
- Réservations : `GET /v1/reservations?listingId=X&checkOutFrom=today&checkOutTo=today`
- Housekeeping : `PUT /v1/reservations/{id}/housekeeping` → `{"status":"clean"}`
