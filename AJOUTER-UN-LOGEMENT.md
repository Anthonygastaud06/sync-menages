# ➕ Ajouter un nouveau logement (procédure simple)

> But : faire en sorte qu'un nouveau logement soit pris en compte par la synchro
> automatique White & Clean → Guesty (logement « Propre », tâche terminée, photos, commentaire).

Il y a **2 IDs à récupérer**, **1 ligne à ajouter**, et (pour les photos/tâches) **1 réglage à vérifier dans Guesty**.

---

## Étape 1 — Récupérer l'ID White & Clean (WAC)

1. Va sur **https://app.whiteandclean.fr** → ouvre la fiche du logement.
2. Regarde l'adresse dans la barre du navigateur :
   ```
   https://app.whiteandclean.fr/portal/customers/appartments/2500
                                                              ▲▲▲▲
                                                      ça = l'ID WAC
   ```
   → Note les chiffres (ex. `2500`).

## Étape 2 — Récupérer l'ID Guesty

1. Va sur **https://app.guesty.com** → ouvre la fiche du même logement.
2. Regarde l'adresse dans la barre du navigateur, l'ID est le long code :
   ```
   https://app.guesty.com/listings/69e2241a7c2afe00139feb8b
                                    ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
                                       ça = l'ID Guesty
   ```
   → Note ce code.

   💡 Vérifie que c'est bien le **même logement** des deux côtés (même adresse).

## Étape 3 — Ajouter la ligne dans `mapping.csv` (sur GitHub)

1. Ouvre **https://github.com/Anthonygastaud06/sync-menages/blob/main/mapping.csv**
2. Clique sur l'icône **crayon** ✏️ (« Edit this file ») en haut à droite.
3. Ajoute une ligne à la fin, au format **`id_wac,id_guesty`** :
   ```
   2500,69e2241a7c2afe00139feb8b
   ```
   (une virgule entre les deux, rien d'autre)
4. Bouton vert **« Commit changes… »** → puis encore **« Commit changes »**.
5. ✅ Fini. Au prochain passage (sous 5 min), le logement est pris en compte.

> Pour **retirer** un logement : même fichier, supprime sa ligne, commit.

## Étape 4 — (pour les photos / tâche / commentaire) Vérifier Guesty

La mise en « Propre » fonctionne dès l'étape 3. Mais pour que la **tâche soit complétée**
et que les **photos + commentaire** s'attachent, il faut qu'une **tâche de ménage existe
dans Guesty** le jour du ménage.

→ Dans Guesty, assure-toi que la **création automatique des tâches de ménage** est activée
pour ce logement (réglage côté Guesty). Sans tâche, le logement passe quand même en « Propre »,
mais sans photos ni commentaire (c'est normal, aucune erreur).

---

## Vérifier que ça a marché

- Va sur **https://github.com/Anthonygastaud06/sync-menages/actions** → ouvre le dernier run.
- Dans l'étape « Run sync », tu dois voir le compteur augmenter, ex :
  `🗺️ 32 appartement(s) dans le mapping` (32 au lieu de 31).
- Le jour d'un ménage terminé, tu verras les lignes `🏠 WAC:2500 → Guesty:…` puis
  `🟢 … 'clean'` et (si tâche) `✅ Tâche … : statut→completed, N photo(s), commentaire`.

## En cas de souci

- **Le logement n'est pas pris en compte** → vérifie la ligne dans `mapping.csv`
  (un seul `,`, pas d'espace, les bons IDs).
- **Logement Propre mais pas de photos/commentaire** → il n'y a pas de tâche Guesty
  ce jour-là (voir Étape 4).
- **Doute sur un ID** → l'ID WAC = chiffres après `/appartments/` ; l'ID Guesty = code
  après `/listings/`.

📄 Doc technique complète : voir `CONTEXT.md`.
