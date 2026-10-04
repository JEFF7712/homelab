import json
import os
from pathlib import Path

from scripts.ci.operation import ROOT, run


def main() -> None:
    catalog = json.loads((ROOT / "config/ci/operations.json").read_text())
    name = os.environ["CI_OPERATION"]
    operation = catalog[name]
    role = "deploy" if operation["mutation"] else "plan"
    for variable, value in operation["environment"].items():
        if isinstance(value, dict):
            key = value["from_secret"].upper()
            if key in {"FORGEJO_PUBLISH_TOKEN", "FORGEJO_SOURCE_READ_TOKEN"}:
                continue
            if key.startswith("STATE_") and key.endswith("_PASSWORD"):
                continue
            os.environ[variable] = os.environ[key]
    # GitLab file variables already contain paths. Convert them to the native operation contract.
    for variable in operation["file_secrets"]:
        os.environ[variable] = Path(os.environ[variable]).read_text()
    os.environ["TF_HTTP_USERNAME"] = "gitlab-" + role
    os.environ["TF_HTTP_PASSWORD"] = os.environ[
        "DR_STATE_" + role.upper() + "_PASSWORD"
    ]
    os.environ["CI_OPERATION_PARENT"] = os.environ["CI_PIPELINE_ID"]
    os.environ["CI_PIPELINE_NUMBER"] = os.environ["CI_PIPELINE_ID"]
    os.environ["CI_OPERATION_AUTHORITY"] = "gitlab"
    # The recovery remote is the authoritative branch while Forgejo is unavailable.
    run(name, catalog)


if __name__ == "__main__":
    main()
