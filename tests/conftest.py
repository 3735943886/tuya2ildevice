import pathlib
import sys

HERE = pathlib.Path(__file__).parent
for p in (HERE, HERE / "golden"):        # `helpers`, and the golden `fixtures` loader
    sys.path.insert(0, str(p))
