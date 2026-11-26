# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["cadquery==2.8.0", "cadquery-ocp==7.9.3.1.1", "vtk==9.6.2", "pyyaml>=6.0"]
# ///
"""CadQuery 2.8 kernel runner for `cad_model.py run`; protocol in cadmodel/runner.py.

Not for direct use: it reads a JSON request on stdin and writes its JSON result to the file the request names.
"""

import sys

from cadmodel.runner import main

if __name__ == "__main__":
    sys.exit(main("cadquery", ("cadquery", "cadquery-ocp", "vtk")))
