"""Configuration commune aux tests.

`main.py` monte le frontend via ``StaticFiles(directory="frontend")``, chemin
résolu par rapport au répertoire courant. En conteneur, le dossier est présent
(`/app/frontend`) ; en local depuis `backend/`, il se trouve à `../frontend`.
On crée alors un lien symbolique temporaire pour que `pytest tests/` fonctionne
dans les deux cas.
"""
import os

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCAL_FRONTEND = os.path.join(_BACKEND_DIR, "frontend")
_REPO_FRONTEND = os.path.join(os.path.dirname(_BACKEND_DIR), "frontend")

if not os.path.isdir(_LOCAL_FRONTEND) and os.path.isdir(_REPO_FRONTEND):
    try:
        os.symlink(_REPO_FRONTEND, _LOCAL_FRONTEND)
    except OSError:
        pass
