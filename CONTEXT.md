# Projet sync-menages — Documentation complète

> Synchronisation automatique : quand un ménage est marqué **terminé** sur
> White & Clean, côté Guesty on met à jour automatiquement :
> - le **logement** → « Propre » (cleaningStatus = clean) ;
> - la **tâche de ménage** du jour → « completed » (ce qui met aussi le logement en Propre) ;
> - les **photos** de la mission → attachées à la tâche (ré-hébergées sur Cloudinary) ;
> - le **commentaire** de la mission → ajouté en commentaire de la tâche (déclenche la notif Guesty).

- **Repo GitHub** : https://github.com/Anthonygastaud06/sync-menages (public)
- **Compte GitHub** : Anthonygastaud06
- **Rythme** : toutes les 5 min (déclenché par cron-job.org)
- **Coût** : 0 € (repo public → GitHub Actions gratuit et illimité)

---

## 1. Comment ça marche (vue d'ensemble)

```
cron-job.org  ──(POST API, toutes les 5 min)──▶  GitHub Actions (workflow sync.yml)
                                                        │
                                                        ▼
                                          python sync_menages.py
                                                        │
        ┌───────────────────────────────────────────────┼───────────────────────────────┐
        ▼                                                 ▼                               ▼
1. Login White & Clean              2. Scrape les missions terminées      3. Pour chaque logement terminé :
   (email + mot de passe)              (classe CSS bg-mission-completed)      mapping.csv → ID Guesty
                                        → récupère l'ID appartement WAC       → PUT cleaningStatus = "clean"
```

Étapes détaillées du script :
1. Connexion à `app.whiteandclean.fr` (vérifie qu'on est réellement authentifié, pas juste un HTTP 200).
2. Scrape `https://app.whiteandclean.fr/portal/customers/missions/reporting`.
3. Détecte les missions terminées via la classe CSS `bg-mission-completed`.
4. Récupère l'ID appartement WAC + l'ID de mission (lien `/missions/reporting/XXXX`).
5. Mappe vers l'ID listing Guesty via `mapping.csv`.
6. Met le listing en `clean` si besoin (`PUT /v1/listings/{id}`).
7. Cherche la **tâche de ménage Guesty du jour** (`GET /v1/tasks` filtré par `listingId` + `dateForSort`).
   Si elle existe, en **un seul PUT** :
   - statut → `completed` (Guesty repasse aussi le logement en `clean`) ;
   - **photos** : récupérées sur la page mission (`…/images/missions/…`), envoyées sur Cloudinary,
     puis ajoutées au tableau `attachments` (dédup par nom de fichier) ;
   - **commentaire** : le commentaire WAC est ajouté dans `comments` avec le préfixe `[WAC]` (dédup par texte).
8. Tout est **idempotent** : aux runs suivants, ce qui est déjà fait n'est pas refait
   (pas de photo en double, pas de re-complétion, pas de re-commentaire).

> Si une mission n'a **pas** de tâche Guesty ce jour-là, seul le `cleaningStatus` du
> logement est mis à jour (photos/commentaire/complétion sont simplement ignorés sans erreur).

---

## 2. Fichiers du projet

| Fichier | Rôle |
|---|---|
| `sync_menages.py` | Script principal (sync toutes les 5 min) |
| `cleanup_cloudinary.py` | Supprime les photos Cloudinary de +90 j (lancé 1×/jour) |
| `mapping.csv` | Correspondance ID WAC → ID Guesty (éditable sur GitHub) |
| `.github/workflows/sync.yml` | Job GitHub Actions de synchro (5 min) |
| `.github/workflows/cleanup.yml` | Job GitHub Actions de nettoyage photos (1×/jour) |
| `.gitignore` | Exclut le log et le cache de token |
| `AJOUTER-UN-LOGEMENT.md` | Procédure simple pour ajouter un logement |
| `CONTEXT.md` | Cette documentation |

> `mapping.xlsx` (s'il est présent en local) est juste un brouillon, **non utilisé** par le script. La seule source de vérité est `mapping.csv`.

---

## 3. Ajouter / retirer un appartement

Tout se passe dans `mapping.csv`, directement depuis le navigateur :
1. Ouvre **https://github.com/Anthonygastaud06/sync-menages/blob/main/mapping.csv**
2. Clique sur l'icône **crayon** (✏️ « Edit this file ») en haut à droite
3. Ajoute une ligne à la fin, au format `id_wac,id_guesty`
   (ex. `2500,69e2241a7c2afe00139feb8b`)
   - **ID WAC** = chiffres de l'URL `…/appartments/XXXX` sur White & Clean
   - **ID Guesty** = chiffres de l'URL du logement dans Guesty
   - Pour **retirer** un appartement : supprime sa ligne
4. Bouton vert **« Commit changes »** (deux fois)
5. Le prochain run (sous 5 min) prend la modif en compte. Aucune virgule/guillemet à gérer.

Le script ignore l'en-tête, les lignes vides, les commentaires (`#`) et les lignes
sans ID Guesty (avec un avertissement dans les logs).

---

## 4. Surveiller / dépanner

- **Voir tous les runs** : https://github.com/Anthonygastaud06/sync-menages/actions
- **Forcer un run manuel** : onglet Actions → workflow « Sync Ménages → Guesty »
  → bouton **« Run workflow »**. (Ou en ligne de commande :
  `gh workflow run "Sync Ménages → Guesty" --repo Anthonygastaud06/sync-menages`)
- **Un run rouge = une vraie erreur** (login échoué, API Guesty en panne…).
  GitHub envoie alors un mail au propriétaire du repo. Un run « 0 mission terminée »
  reste vert (c'est normal).
- **Lire le détail** : clique sur un run → étape « Run sync » → les logs expliquent
  ce qui a été détecté et mis à jour.

Erreurs côté cron-job.org (visibles dans son historique d'exécution) :
| Code | Signification | À corriger |
|---|---|---|
| **204** | ✅ Succès | rien |
| **401** | Token refusé | header `Authorization` = `Bearer ` + token complet (un espace, pas de guillemets) |
| **403** | Token sans droits | permission **Actions: Read and write** sur le token |
| **404** | URL ou accès incorrect | vérifier l'URL exacte / le repo coché dans le token |
| **422** | Corps invalide | le corps doit être `{"ref":"main"}` |

---

## 5. Le déclencheur cron-job.org (à recréer si besoin)

Le workflow GitHub a un cron interne (`*/5`) mais GitHub **ne le respecte pas**
de façon fiable (souvent ~1×/h). On utilise donc **cron-job.org** (gratuit) pour
appeler l'API GitHub toutes les 5 min de façon fiable.

**Configuration du cronjob :**
- **URL** : `https://api.github.com/repos/Anthonygastaud06/sync-menages/actions/workflows/sync.yml/dispatches`
- **Schedule** : Every 5 minutes
- **Méthode** : POST
- **Corps** : `{"ref":"main"}`
- **Headers** :
  | Nom | Valeur |
  |---|---|
  | `Authorization` | `Bearer github_pat_…` (token GitHub) |
  | `Accept` | `application/vnd.github+json` |
  | `X-GitHub-Api-Version` | `2022-11-28` |

**Créer le token GitHub** (https://github.com/settings/personal-access-tokens/new) :
- Fine-grained token, **Repository access** = `sync-menages` uniquement
- **Permissions → Actions = Read and write**
- Expiration max 1 an → **à renouveler** avant expiration (sinon le déclencheur tombe en 401)

---

## 6. Secrets GitHub Actions

Configurés dans : repo → Settings → Secrets and variables → Actions.
Ils sont **chiffrés** et invisibles, même si le repo est public.

| Secret | Rôle |
|---|---|
| `WAC_EMAIL` | Email de connexion White & Clean |
| `WAC_PASSWORD` | Mot de passe White & Clean |
| `GUESTY_CLIENT_ID` | Client ID de l'intégration OAuth Guesty |
| `GUESTY_CLIENT_SECRET` | Client Secret Guesty |
| `CLOUDINARY_CLOUD_NAME` | Cloud name Cloudinary (ré-hébergement des photos) |
| `CLOUDINARY_API_KEY` | API Key Cloudinary |
| `CLOUDINARY_API_SECRET` | API Secret Cloudinary |

> Les 3 secrets Cloudinary sont **optionnels** : s'ils sont absents, la synchro des
> photos est ignorée (le reste — cleaningStatus, complétion de tâche, commentaire — continue).

**Mettre à jour un secret** (ex. après rotation d'un mot de passe) :
```bash
gh secret set WAC_PASSWORD --repo Anthonygastaud06/sync-menages
gh secret set GUESTY_CLIENT_SECRET --repo Anthonygastaud06/sync-menages
```

---

## 7. API Guesty (Open API) — référence technique

- **Auth** : `POST https://open-api.guesty.com/oauth2/token`
  (form-urlencoded, `grant_type=client_credentials`, `scope=open-api`)
  ⚠️ Max **5 tokens / 24h / clientId**, token valable 24h → mis en cache dans
  `.guesty_token.json`, persisté entre les runs via `actions/cache`.
- **Lire le statut** : `GET /v1/listings/{id}?fields=cleaningStatus`
- **Marquer propre** : `PUT /v1/listings/{id}` → `{"cleaningStatus": {"value": "clean"}}`
- **Valeurs** `cleaningStatus.value` : `clean` (Propre) · `dirty` (Sale) ·
  `waiting_for_inspection` (En attente d'inspection) · `unknown` (Inconnu) · non défini

> Le statut de ménage est porté par le **listing**, pas par la réservation.

**Tâches de ménage (cleaning tasks) :**
- **Chercher** : `GET /v1/tasks?filters=[…]` — filtres en JSON, ex. :
  `[{"field":"listingId","operator":"$eq","value":"<id>"},
    {"field":"dateForSort","operator":"$gte","value":"<jour>T00:00:00.000Z"},
    {"field":"dateForSort","operator":"$lte","value":"<jour>T23:59:59.999Z"}]`
- **Mettre à jour** : `PUT /v1/tasks/{id}` avec un corps combinant les champs :
  - `status` ∈ `pending, confirmed, in progress, completed, canceled`
    → passer à **`completed`** met aussi le logement en `clean` (effet automatique Guesty).
  - `attachments` : tableau `[{url, title, mimetype}]` — accepte des **URLs externes**
    (le PUT **remplace** tout le tableau → renvoyer l'existant + les nouvelles).
  - `comments` : tableau `[{text}]` — le PUT **remplace** aussi tout le tableau.
- ⚠️ Pas d'endpoint d'ajout unitaire (POST …/comments → 404) : on relit puis on renvoie le tableau complet.

**Cloudinary (ré-hébergement photos) :**
- `POST https://api.cloudinary.com/v1_1/{cloud}/image/upload` (signed) — paramètre `file`
  = URL externe (Cloudinary va chercher l'image), `public_id` déterministe + `overwrite=true`
  (idempotent), `signature` = SHA1 de `overwrite=true&public_id=…&timestamp=…` + API secret.
- Renvoie `secure_url` (URL publique permanente) → c'est elle qu'on attache à la tâche.

---

## 8. White & Clean — référence technique

- **Login** : `https://app.whiteandclean.fr/portal/customers/login` (email + password,
  + jeton CSRF `_token` si présent dans le formulaire).
- **Missions** : `https://app.whiteandclean.fr/portal/customers/missions/reporting`.
- **Mission terminée** = `<div>` avec la classe CSS `bg-mission-completed`.
- L'ID appartement est dans le lien `…/appartments/XXXX` à l'intérieur de la mission.

Exemple de structure HTML :
```html
<div class="row pb-2 mx-0 bg-mission-completed">
  <a href="/portal/customers/appartments/2329">
    <span>12 BOULEVARD JEAN JAURÈS 06300 NICE</span>
  </a>
  <a href="/portal/customers/missions/reporting/102594"> … </a>
</div>
```

**Page détail d'une mission** : `…/missions/reporting/{mission_id}`
- **Photos** : dans des `<div class="gallery"><a href="…/images/missions/{id}/….jpg">…`.
  Elles sont **publiques** (accessibles sans login) et **permanentes** (pas d'expiration).
- **Commentaire** : dans la carte dont le `<h5 class="card-title">` vaut « Commentaire »
  (le texte est le contenu de la `card-body` moins le titre). Vide → pas de commentaire.

> Note : les photos de **référence d'un appartement** (page `/appartments/{id}`) sont, elles,
> sur DigitalOcean Spaces avec des URLs **signées qui expirent en 5h** — ce ne sont PAS les
> photos de mission (qui sont sur `app.whiteandclean.fr/images/missions/…`, permanentes).

---

## 9. Robustesse intégrée au script

- Code de sortie **non nul si erreur** → job GitHub rouge + notification mail.
  (« 0 mission terminée » reste un succès.)
- **Login vérifié** : une mauvaise authentification renvoie un HTTP 200 (page de login
  réaffichée) → le script vérifie l'URL finale pour détecter le vrai échec.
- **Timeouts** de 30 s sur toutes les requêtes.
- **Retries** automatiques (3) sur erreurs transitoires (429 / 5xx).
- **Token Guesty rejeté (401)** → régénéré et requête rejouée une fois.
- **Isolation par logement** : un échec sur un appartement n'arrête pas les autres ;
  bilan d'erreurs en fin de run.

---

## 10. Exécution locale (optionnel)

```bash
pip install requests beautifulsoup4 schedule
export WAC_EMAIL=... WAC_PASSWORD=... GUESTY_CLIENT_ID=... GUESTY_CLIENT_SECRET=...
python sync_menages.py            # une fois (exit 1 si erreur)
python sync_menages.py --loop     # boucle toutes les 5 min (machine allumée requise)
```

---

## 11. Notes d'hébergement / coût

- Le repo est **public** → GitHub Actions est **gratuit et illimité** (les repos
  privés n'ont que 2000 min/mois gratuites, insuffisant pour un run toutes les 5 min).
- Les secrets restent chiffrés même en public ; seuls le code, `mapping.csv` (IDs)
  et les exemples de cette doc sont visibles publiquement.
- L'automatisation tourne **entièrement sur GitHub** : ton ordinateur peut être éteint.
