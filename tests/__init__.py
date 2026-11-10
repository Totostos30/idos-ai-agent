"""All test modules share one throw-away IDOS_HOME, set before core is imported."""
import os
import tempfile

os.environ.setdefault("IDOS_HOME", tempfile.mkdtemp(prefix="idos-tests-"))
