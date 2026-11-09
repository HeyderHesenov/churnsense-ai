"""Print the API's OpenAPI schema as stable JSON.

The front end's TypeScript types are generated from this output
(`npm run openapi` in frontend/), and CI regenerates them and fails if the
committed file differs - so the browser's idea of every response shape is the
server's, never a hand-written copy.
"""

from __future__ import annotations

import json
import sys

from churnsense.api.main import app


def main() -> int:
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
