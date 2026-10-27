"""Unit tests for the voice-id proxy transcript sanity guard.

Covers is_garbage_transcript from scripts.voice_id.proxy. These tests use
only the standard library so they run on the host; the classifier tests in
test_voice_id.py need numpy/sherpa and run in the container.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from scripts.voice_id.proxy import format_transcript_with_speaker, is_garbage_transcript


class TestTranscriptGuard(unittest.TestCase):
    def test_empty_and_whitespace_dropped(self) -> None:
        self.assertTrue(is_garbage_transcript(""))
        self.assertTrue(is_garbage_transcript("   "))

    def test_repeated_token_dropped(self) -> None:
        self.assertTrue(is_garbage_transcript("Govee, Govee, Govee, Govee,"))
        self.assertTrue(is_garbage_transcript("Govee,"))
        self.assertTrue(is_garbage_transcript("no no no no"))

    def test_known_hallucinations_dropped(self) -> None:
        for text in (
            "Stu.",
            "Control.",
            "Further.",
            "Chew away.",
            "Say something.",
            "Today, whoever.",
            "You wait. Hey Jarvis, who am I?",
            "Govee, V",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_garbage_transcript(text))

    def test_real_commands_kept(self) -> None:
        for text in (
            "Stop.",
            "Hey Jarvis, stop.",
            "Hey Jarvis.",
            "Turn off the downstairs lights.",
            "Lower your volume by 10%.",
            "What's my name?",
            "Done.",
            "Play some Kanye.",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_garbage_transcript(text))

    def test_two_repeats_kept(self) -> None:
        self.assertFalse(is_garbage_transcript("okay okay"))


def _unprefixed_automation_commands(filename: str) -> list[str]:
    root = Path(__file__).resolve().parent.parent
    doc = yaml.safe_load(
        (root / "home-assistant" / "automations" / filename).read_text(encoding="utf-8")
    )
    commands: list[str] = []
    for trigger in doc.get("triggers", []):
        for command in trigger.get("command", []):
            if command.startswith("[speaker {speaker}]"):
                continue
            commands.append(command)
    return commands


class TestSpeakerPrefixContract(unittest.TestCase):
    def test_speaker_prefix_covers_music_commands(self) -> None:
        commands = _unprefixed_automation_commands("jarvis_voice_music_playback.yaml")
        self.assertTrue(commands)
        for command in commands:
            say = command.replace("{query}", "travis scott")
            with self.subTest(command=command):
                self.assertTrue(
                    format_transcript_with_speaker(say, "Sam").startswith(
                        "speaker Sam "
                    ),
                    f"proxy never prefixes {command!r}; "
                    "Sam's request falls back to Spotify without his name",
                )

    def test_speaker_prefix_covers_identity_commands(self) -> None:
        commands = _unprefixed_automation_commands("jarvis_voice_identity.yaml")
        self.assertTrue(commands)
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(
                    format_transcript_with_speaker(command, "Rupan").startswith(
                        "speaker Rupan "
                    ),
                    f"proxy never prefixes {command!r}; automation answers "
                    "'I don\\u2019t recognize your voice' despite voice-ID success",
                )


class TestVoiceIdConfigMapGenerator(unittest.TestCase):
    def test_no_configmap_ships_voice_id_data(self) -> None:
        # Speaker embeddings are biometric data and ship only via the
        # SOPS-encrypted voice-id-profiles Secret. Nemotron bakes the proxy
        # into its image, so no ConfigMap carries voice-id files.
        root = Path(__file__).resolve().parent.parent
        kust = yaml.safe_load(
            (root / "gitops" / "voice" / "kustomization.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("voice-id.yaml", kust.get("resources", []))
        for entry in kust.get("configMapGenerator", []):
            for f in entry.get("files", []):
                self.assertFalse(f.startswith("voice-id/"), f)

    def test_profiles_ship_via_encrypted_secret(self) -> None:
        root = Path(__file__).resolve().parent.parent
        secrets_kust = yaml.safe_load(
            (root / "gitops" / "secrets" / "kustomization.yaml").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("voice-id-profiles.sops.yaml", secrets_kust.get("resources", []))
        for manifest in ("stt-nemotron.yaml",):
            text = (root / "gitops" / "voice" / manifest).read_text(encoding="utf-8")
            self.assertIn("secretName: voice-id-profiles", text, manifest)
            self.assertIn("mountPath: /etc/voice-id", text, manifest)
            self.assertIn("/etc/voice-id/profiles.json", text, manifest)
            self.assertNotIn("subPath", text, manifest)

    def test_scripts_symlink_tracks_deploy_source(self) -> None:
        root = Path(__file__).resolve().parent.parent
        link = root / "scripts" / "voice_id" / "proxy.py"
        self.assertTrue(link.is_symlink(), "proxy.py")
        self.assertEqual(
            link.resolve(),
            (root / "gitops" / "voice" / "voice-id" / "proxy.py").resolve(),
            "proxy.py",
        )

    def test_local_profiles_symlink_when_present(self) -> None:
        # profiles.json is gitignored local-only data (SOPS Secret in
        # git); when an enrolled copy exists it must track the deploy
        # source, and clean checkouts without one must still pass.
        root = Path(__file__).resolve().parent.parent
        link = root / "scripts" / "voice_id" / "profiles.json"
        if not link.is_symlink():
            self.skipTest("no local profiles.json enrolled")
        self.assertEqual(
            link.resolve(),
            (root / "gitops" / "voice" / "voice-id" / "profiles.json").resolve(),
            "profiles.json",
        )


if __name__ == "__main__":
    unittest.main()
