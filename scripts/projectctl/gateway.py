from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .codec import MAX_BYTES, loads
from .config import Configuration
from .errors import Code, ProtocolError, success
from .runtime import Runtime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--principal", required=True)
    args = parser.parse_args()
    try:
        runtime = Runtime(Configuration.load(args.config))
        result = runtime.handle(
            args.principal, loads(sys.stdin.buffer.read(MAX_BYTES + 1))
        )
        response, code = success(result), 0
    except ProtocolError as error:
        response, code = error.envelope(), error.exit_code
    except Exception:
        error = ProtocolError(Code.INTERNAL_ERROR)
        response, code = error.envelope(), error.exit_code
    print(json.dumps(response, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
