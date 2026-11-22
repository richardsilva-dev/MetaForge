from __future__ import annotations
import json
from pathlib import Path
from typing import Any

def save_run(result:dict[str,Any],destination:Path)->Path:
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix(destination.suffix+".tmp")
    temporary.write_text(json.dumps(result,indent=2,sort_keys=True),encoding="utf-8")
    temporary.replace(destination)
    return destination
