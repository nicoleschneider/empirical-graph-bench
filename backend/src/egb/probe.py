import importlib.metadata
import json
import platform
import sys

print(
    json.dumps(
        {
            "python": platform.python_version(),
            "executable": sys.executable,
            "platform": platform.platform(),
            "packages": sorted(
                f"{dist.metadata['Name']}=={dist.version}"
                for dist in importlib.metadata.distributions()
            ),
        }
    )
)
