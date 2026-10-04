from importlib.resources import files

from pydantic import BaseModel, ConfigDict

PROMPT_VERSION = "classify-v1"


class Prompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str
    text: str


def load_prompt(version: str = PROMPT_VERSION) -> Prompt:
    """Read a prompt file by version. A changed prompt gets a new version, never an edit."""
    path = files(__package__).joinpath(f"{version}.md")
    if not path.is_file():
        raise ValueError(f"unknown prompt version: {version}")
    return Prompt(version=version, text=path.read_text(encoding="utf-8"))
