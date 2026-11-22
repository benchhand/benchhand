# SPDX-FileCopyrightText: 2026 Sjors Robroek
#
# SPDX-License-Identifier: MPL-2.0

"""The workflow stages in order, as `product.schema.json` defines them: the one reader of that list.

It imports only the standard library, so `validate.py` and `decision_records.py` can both load it by path
(registered in `sys.modules` as `cad_record_stages`) without importing each other."""

from __future__ import annotations

import json
from pathlib import Path

PRODUCT_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "product.schema.json"


def stages() -> tuple[str, ...]:
    """The workflow stages in order."""
    schema = json.loads(PRODUCT_SCHEMA.read_text(encoding="utf-8"))
    return tuple(schema["$defs"]["stage"]["enum"])
