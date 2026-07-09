"""Exportador JUnit XML para integração CI/CD."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Optional


class JUnitExporter:
    """Exporta resultados de stress/homologação em formato JUnit XML.

    Compatível com Jenkins, GitLab CI, GitHub Actions, Azure DevOps.
    """

    @staticmethod
    def export(
        stress_result: Any,  # StressRunResult
        journey_name: str = "",
        suite_name: str = "synthetic-stress",
        threshold_pct: float = 90.0,
    ) -> str:
        """Exporta resultado de stress como JUnit XML.

        Args:
            stress_result: StressRunResult
            journey_name: Nome da jornada
            suite_name: Nome do test suite
            threshold_pct: Taxa mínima de sucesso para considerar PASS
        """
        total = max(1, stress_result.total_sessions)
        success_rate = round(stress_result.completed / total * 100, 1)

        testsuite = ET.Element("testsuite", {
            "name": suite_name,
            "tests": str(stress_result.total_sessions),
            "failures": str(stress_result.failed),
            "errors": str(stress_result.errors),
            "skipped": "0",
            "time": str(round(stress_result.duration_ms / 1000, 2)),
            "timestamp": datetime.now().isoformat(),
            "hostname": "replay-runner",
        })

        # Propriedades
        props = ET.SubElement(testsuite, "properties")
        ET.SubElement(props, "property", {
            "name": "journey_name", "value": journey_name,
        })
        ET.SubElement(props, "property", {
            "name": "success_rate_pct", "value": str(success_rate),
        })
        ET.SubElement(props, "property", {
            "name": "threshold_pct", "value": str(threshold_pct),
        })
        ET.SubElement(props, "property", {
            "name": "overall_status",
            "value": "PASS" if success_rate >= threshold_pct else "FAIL",
        })

        if stress_result.aggregate_verification:
            av = stress_result.aggregate_verification
            ET.SubElement(props, "property", {
                "name": "overall_pass_rate_pct",
                "value": str(av.get("overall_pass_rate_pct", 0)),
            })
            ET.SubElement(props, "property", {
                "name": "total_errors",
                "value": str(av.get("total_errors", 0)),
            })

        # Testcases por sessão
        for sr in stress_result.session_results:
            tc = ET.SubElement(testsuite, "testcase", {
                "name": f"session-{sr.session_index}",
                "classname": f"{suite_name}.{journey_name or 'journey'}",
                "time": str(round(sr.duration_ms / 1000, 3)),
            })

            if sr.status == "failed":
                error_msg = ""
                error_type = "functional"
                if sr.errors:
                    first = sr.errors[0] if isinstance(sr.errors[0], dict) else {}
                    error_msg = first.get("message", str(sr.errors[0]))
                    error_type = first.get("type", "functional")

                failure = ET.SubElement(tc, "failure", {
                    "type": error_type,
                    "message": f"Sessão {sr.session_index} falhou",
                })
                failure.text = error_msg

            elif sr.status == "error":
                error = ET.SubElement(tc, "error", {
                    "type": "technical_error",
                    "message": f"Sessão {sr.session_index} erro técnico",
                })
                if sr.errors:
                    error.text = str(sr.errors[0]) if not isinstance(sr.errors[0], dict) else sr.errors[0].get("message", "")

            # System-out com script replay
            if hasattr(sr, 'replay_script') and sr.replay_script:
                so = ET.SubElement(tc, "system-out")
                so.text = sr.replay_script[:2000]

        # Resumo agregado
        if stress_result.aggregate_verification:
            av = stress_result.aggregate_verification
            tc_summary = ET.SubElement(testsuite, "testcase", {
                "name": "aggregate-summary",
                "classname": f"{suite_name}.summary",
                "time": "0",
            })

            if success_rate < threshold_pct:
                failure = ET.SubElement(tc_summary, "failure", {
                    "type": "threshold_not_met",
                    "message": f"Success rate {success_rate}% below threshold {threshold_pct}%",
                })
                failure.text = f"Expected >= {threshold_pct}%, got {success_rate}%"

        # Formatar XML
        ET.indent(testsuite, space="  ")
        return ET.tostring(testsuite, encoding="unicode", xml_declaration=True)

    @staticmethod
    def export_macro(
        macro_result: Any,  # MacroJourneyResult
        suite_name: str = "macro-journey",
        threshold_pct: float = 90.0,
    ) -> str:
        """Exporta resultado de macro-jornada como JUnit XML com subsuites por módulo."""
        testsuites = ET.Element("testsuites", {
            "name": suite_name,
            "tests": str(macro_result.total_sessions),
            "failures": str(macro_result.total_failed),
            "errors": str(macro_result.total_errors),
            "time": str(round(macro_result.duration_ms / 1000, 2)),
        })

        for module_name, module_result in macro_result.module_results.items():
            xml_str = JUnitExporter.export(
                module_result,
                journey_name=module_name,
                suite_name=f"{suite_name}.{module_name}",
                threshold_pct=threshold_pct,
            )
            # Parse e adicionar ao testsuites
            suite_elem = ET.fromstring(xml_str)
            testsuites.append(suite_elem)

        ET.indent(testsuites, space="  ")
        return ET.tostring(testsuites, encoding="unicode", xml_declaration=True)

    @staticmethod
    def save_xml(xml_content: str, output_path: str) -> str:
        """Salva XML em arquivo."""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(xml_content)
        return output_path
