# Flux

Les nouvelles vidéos de mes chaînes YouTube, rien d'autre : pas de Shorts, pas de commentaires, pas de recommandations. Les vidéos se regardent dans l'app.

## Comment ça marche

- `channels.json` : la liste de mes chaînes (un `@nom` par chaîne).
- `check.py` : lit les flux RSS publics de YouTube et écrit `videos.json` (sans les Shorts).
- `.github/workflows/check.yml` : GitHub lance `check.py` toutes les ~15 minutes, gratuitement.
- `index.html` : l'app (PWA pour l'iPhone) qui affiche `videos.json`.

## Mise en ligne

1. Créer un dépôt GitHub public nommé `flux` et y mettre tous les fichiers de ce dossier.
2. Settings → Pages → Source : `Deploy from a branch` → branche `main`, dossier `/ (root)`.
3. Settings → Actions → General → Workflow permissions → `Read and write permissions`.
4. Onglet Actions → « Vérifier les vidéos » → Run workflow (une première fois pour créer `videos.json`).
5. Sur l'iPhone : ouvrir `https://mathematicien100frontieres.github.io/flux/` dans Safari → Partager → Sur l'écran d'accueil.

## Changer de chaînes

Modifier `channels.json` sur GitHub (icône crayon), puis relancer « Vérifier les vidéos ».
