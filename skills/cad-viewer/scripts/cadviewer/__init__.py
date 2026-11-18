# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""cad-viewer: drive OCP CAD Viewer and its side panel for a cad-model hardware record.

`record` and `motion` are kernel-free (PyYAML only) so the command layer tests run on plain
python3; `host` and `capture` import ocp_viewer, OCP and Playwright lazily.
"""


class ViewerError(RuntimeError):
    """Inputs or infrastructure the command cannot use; maps to exit 2."""


class LimitError(ViewerError):
    """A requested joint value lies outside its assembly.yaml limits; maps to exit 1."""

    def __init__(self, message: str, findings: list[dict[str, object]]) -> None:
        super().__init__(message)
        self.findings = findings
