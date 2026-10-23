"""Tests de la mise à jour du serveur (bouton « Mise à jour du serveur »).

Couvre :
- l'alignement sur origin/main (robuste à un historique distant réécrit) ;
- la conservation des fichiers non suivis (.env, docker-compose.override.yml) ;
- le téléchargement des nouvelles images Caddy / cloudflared ;
- le redémarrage du backend quand Caddy est recréé (routes à recharger) ;
- la détection de mise à jour par contenu plutôt que par historique.
"""
import os

os.environ.setdefault("DOMAIN", "test.local")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "x")

import asyncio

from routers import system


class TestUpdaterScript:
    def setup_method(self):
        self.script = system._updater_script()

    def test_aligns_on_origin_instead_of_merging(self):
        assert "git pull" not in self.script
        assert self.script.index("git fetch origin main") < self.script.index("git reset --hard origin/main")

    def test_keeps_untracked_files(self):
        assert "git clean" not in self.script

    def test_pulls_published_images_and_refreshes_base_image(self):
        assert "docker compose pull --ignore-buildable" in self.script
        assert "docker compose build --pull" in self.script
        assert self.script.index("docker compose pull") < self.script.index("docker compose up -d")

    def test_restarts_backend_when_caddy_recreated(self):
        up = self.script.index("docker compose up -d")
        assert self.script.index('caddy_before="$(docker compose ps -q caddy)"') < up
        assert self.script.index("docker compose restart backend") > up

    def test_steps_are_chained(self):
        # Une étape en échec doit arrêter la suite (pas de `up` après un fetch raté).
        # Entre l'installation de git/ssh et le test final sur Caddy, tout est
        # enchaîné par `&&` : aucun `;` qui continuerait malgré une erreur.
        middle = self.script.split("fi && ", 1)[1].split("if [", 1)[0]
        assert ";" not in middle


class TestUpdateCheck:
    def _run(self, monkeypatch, outputs):
        async def fake_git(*args):
            return outputs.get(args, (0, ""))
        monkeypatch.setattr(system, "_git_output", fake_git)
        return asyncio.run(system.update_check())

    def test_rewritten_history_with_same_content_is_not_an_update(self, monkeypatch):
        res = self._run(monkeypatch, {
            ("rev-parse", "HEAD"): (0, "a" * 40),
            ("rev-parse", "origin/main"): (0, "b" * 40),
            ("rev-list", "--count", "HEAD..origin/main"): (0, "51"),
            ("rev-parse", "HEAD^{tree}"): (0, "t" * 40),
            ("rev-parse", "origin/main^{tree}"): (0, "t" * 40),
        })
        assert res["update_available"] is False

    def test_new_content_is_an_update(self, monkeypatch):
        res = self._run(monkeypatch, {
            ("rev-parse", "HEAD"): (0, "a" * 40),
            ("rev-parse", "origin/main"): (0, "b" * 40),
            ("rev-list", "--count", "HEAD..origin/main"): (0, "2"),
            ("rev-parse", "HEAD^{tree}"): (0, "t" * 40),
            ("rev-parse", "origin/main^{tree}"): (0, "u" * 40),
        })
        assert res["update_available"] is True
        assert res["commits_behind"] == 2
