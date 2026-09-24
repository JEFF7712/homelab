from __future__ import annotations

import unittest

from scripts import voice_topology


def _pipeline_payload() -> dict:
    return {
        "preferred_pipeline": "preferred",
        "pipelines": [
            {
                "id": "preferred",
                "name": "Jarvis",
                "language": "en",
                "conversation_engine": "conversation.jarvis_jev",
                "stt_engine": "stt.faster_whisper",
                "tts_engine": "tts.chatterbox_turbo",
                "tts_voice": "jarvis",
                "wake_word_entity": None,
            },
            {
                "id": "legacy",
                "name": "Legacy",
                "language": "en",
                "conversation_engine": "conversation.home_assistant",
                "stt_engine": "stt.ghost_engine",
                "tts_engine": "tts.piper",
                "tts_voice": "alan",
                "wake_word_entity": None,
            },
        ],
    }


def _states() -> list:
    return [
        {
            "entity_id": "conversation.jarvis_jev",
            "state": "unknown",
            "attributes": {},
        },
        {"entity_id": "stt.faster_whisper", "state": "unknown", "attributes": {}},
        {"entity_id": "tts.chatterbox_turbo", "state": "unknown", "attributes": {}},
        {
            "entity_id": "conversation.home_assistant",
            "state": "unknown",
            "attributes": {},
        },
        {"entity_id": "tts.piper", "state": "unknown", "attributes": {}},
        {
            "entity_id": voice_topology.SATELLITE_ENTITY,
            "state": "idle",
            "attributes": {},
        },
    ]


class VoiceTopologyTest(unittest.TestCase):
    def test_report_contains_gateway_order_and_service_targets(self) -> None:
        report = voice_topology.build_report()
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(
            report["desired"]["gateways"]["stt"]["backends"][0]["name"], "nemotron"
        )
        self.assertEqual(
            report["desired"]["gateways"]["tts"]["backends"][1]["name"], "piper"
        )
        self.assertEqual(
            report["desired"]["ha_pipeline"]["conversation_agent"], "Jarvis Jev Router"
        )
        self.assertEqual(report["live"]["status"], "unknown")

    def test_desired_invariants_pass(self) -> None:
        self.assertEqual(voice_topology.check(voice_topology.build_report()), [])

    def test_desired_report_flags_backends_without_services(self) -> None:
        gateways = {
            "tts": {
                "deployment": "wyoming-tts-gateway",
                "backends": [
                    {
                        "name": "stale",
                        "host": "wyoming-piper.voice.svc.cluster.local",
                        "port": 10200,
                    },
                    {"name": "ok", "host": "wyoming-tts-piper", "port": 10200},
                ],
            }
        }
        findings = voice_topology.backend_findings(gateways, {"wyoming-tts-piper"})
        self.assertEqual(len(findings), 1)
        self.assertIn("wyoming-piper", findings[0])

    def test_current_manifests_have_no_stale_backend_findings(self) -> None:
        report = voice_topology.build_report()
        self.assertEqual(report["desired"]["findings"], [])

    def test_pipeline_summary_resolves_preferred_and_engines(self) -> None:
        summary = voice_topology.summarize_pipelines(_pipeline_payload(), _states())
        self.assertEqual(summary["status"], "known")
        self.assertEqual(summary["preferred_pipeline"], "preferred")
        self.assertIsNone(summary["selected_pipeline"])
        self.assertIn("unknown", summary["selected_evidence"])
        self.assertIn("preferred", summary["selected_evidence"])
        by_id = {item["id"]: item for item in summary["pipelines"]}
        self.assertTrue(by_id["preferred"]["preferred"])
        self.assertFalse(by_id["legacy"]["preferred"])
        self.assertEqual(by_id["preferred"]["tts_voice"], "jarvis")
        self.assertEqual(
            by_id["preferred"]["conversation_engine"], "conversation.jarvis_jev"
        )

    def test_pipeline_summary_flags_missing_engine_entities(self) -> None:
        summary = voice_topology.summarize_pipelines(_pipeline_payload(), _states())
        self.assertEqual(len(summary["missing_engine_entities"]), 1)
        self.assertIn("stt.ghost_engine", summary["missing_engine_entities"][0])

    def test_pipeline_summary_reports_unknown_evidence_honestly(self) -> None:
        summary = voice_topology.summarize_pipelines({"unexpected": True}, None)
        self.assertEqual(summary["status"], "unknown")
        self.assertIn("reason", summary)

    def test_pipeline_summary_uses_satellite_selection_when_present(self) -> None:
        states = _states()
        for state in states:
            if state["entity_id"] == voice_topology.SATELLITE_ENTITY:
                state["attributes"] = {"selected_pipeline": "legacy"}
        summary = voice_topology.summarize_pipelines(_pipeline_payload(), states)
        self.assertEqual(summary["selected_pipeline"], "legacy")
        self.assertIn("satellite entity", summary["selected_evidence"])

    def test_live_without_credentials_reports_unknown_with_reason(self) -> None:
        summary = voice_topology.fetch_live_pipelines("http://127.0.0.1:1", "", 1.0)
        self.assertEqual(summary["status"], "unknown")
        self.assertIn("reason", summary)


if __name__ == "__main__":
    unittest.main()
