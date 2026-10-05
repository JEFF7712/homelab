from __future__ import annotations

import socket
import unittest
from contextlib import redirect_stdout
from io import StringIO

from scripts.ci.nas_proof import prove


class NasProofTest(unittest.TestCase):
    def test_listening_endpoint_is_proven(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            host, port = listener.getsockname()
            output = StringIO()
            with redirect_stdout(output):
                prove([(host, port)])
            self.assertIn(f"Connected to {host}:{port}", output.getvalue())

    def test_unavailable_endpoint_fails_the_diagnostic(self) -> None:
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            output = StringIO()
            with redirect_stdout(output), self.assertRaises(OSError):
                prove([reserved.getsockname()])
            self.assertNotIn("Connected to", output.getvalue())


if __name__ == "__main__":
    unittest.main()
