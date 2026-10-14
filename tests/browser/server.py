from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ladies_v9.app import create_app


root = Path(tempfile.mkdtemp(prefix="ladies-v13-browser-"))
app = create_app(db_path=root / "app.sqlite3", data_dir=root / "data")

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("LADIES_BROWSER_TEST_PORT", "8765")),
        debug=False,
        use_reloader=False,
        threaded=True,
    )
