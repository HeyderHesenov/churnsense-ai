"""Print the ``docker build`` command whose tag and labels say exactly what is built.

An image tagged with a commit must be that commit. The build context, though,
is the working tree: an uncommitted change in it would be built under the
commit's name. So the tag and the OCI labels come from the tree's real state:

* clean (no change, staged or not, and no untracked file under any path the
  build context lets in): ``churnsense-ai:<12-char commit>``, dirty=false;
* otherwise: ``churnsense-ai:<12-char commit>-dirty``, dirty=true - never the
  clean tag.

The paths that count are the ones ``.dockerignore`` re-includes, plus the
Dockerfile and ``.dockerignore`` themselves: a change anywhere else (an
untracked ``audit-reports/``, say) is not in the image and does not make it
dirty. The model artifact is ignored by git, so git cannot vouch for it; the
build's own bundle check (``verify_deploy_bundle.py --in-image``) checks its hash.

The command always ends with the build context ``.``, exactly once and as its
last argument. ``.`` means the directory the command is run in, so it must be
run from the repository root - the directory this script names on stderr.

Read-only: it runs ``git rev-parse`` and ``git status`` and prints. It never
runs Docker - copy the printed command (docs/DEPLOYMENT.md, section 8).
"""

from __future__ import annotations

import hashlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "churnsense-ai"
#: The build context: the repository root, as the directory the command runs in.
CONTEXT = "."


@dataclass(frozen=True)
class Provenance:
    revision: str
    dirty: bool
    #: The sha256 of ``artifacts/public_evidence.json``: git ignores the file, so the
    #: revision cannot name it; the image labels this instead, and the build checks it.
    evidence_sha256: str = ""

    @property
    def tag(self) -> str:
        short = self.revision[:12]
        return f"{IMAGE}:{short}-dirty" if self.dirty else f"{IMAGE}:{short}"

    def build_command(self, *extra: str) -> list[str]:
        """The command, ending with the one build context. ``extra`` is build options
        only: a context among them would make two, so one given there is refused."""
        if CONTEXT in extra:
            raise ValueError("the build context '.' is added already; pass build options only")
        return [
            "docker",
            "build",
            "--tag",
            self.tag,
            "--build-arg",
            f"SOURCE_REVISION={self.revision}",
            "--build-arg",
            f"SOURCE_DIRTY={'true' if self.dirty else 'false'}",
            "--build-arg",
            f"EVIDENCE_SHA256={self.evidence_sha256}",
            *extra,
            CONTEXT,
        ]


def context_paths(dockerignore: str) -> list[str]:
    """What the build context lets in: each ``!`` rule, plus the two build files."""
    included = [
        line.strip()[1:].rstrip("/")
        for line in dockerignore.splitlines()
        if line.strip().startswith("!")
    ]
    return sorted({*included, "Dockerfile", ".dockerignore"})


def provenance(head: str, porcelain: str, evidence_sha256: str = "") -> Provenance:
    """``head`` from ``git rev-parse HEAD``; ``porcelain`` from ``git status --porcelain``
    limited to the context paths. Any line at all - modified, staged, deleted or
    untracked - makes the build dirty."""
    revision = head.strip()
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("HEAD is not a full commit id")
    return Provenance(
        revision=revision, dirty=bool(porcelain.strip()), evidence_sha256=evidence_sha256
    )


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def current() -> Provenance:
    paths = context_paths((ROOT / ".dockerignore").read_text(encoding="utf-8"))
    head = _git("rev-parse", "HEAD")
    porcelain = _git("status", "--porcelain", "--untracked-files=all", "--", *paths)
    return provenance(head, porcelain, evidence_hash(ROOT / EVIDENCE))


#: The published evidence the image bakes in.
EVIDENCE = Path("artifacts") / "public_evidence.json"


def evidence_hash(path: Path) -> str:
    """The file's sha256, or ``""`` when there is none: the build then refuses."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def main(argv: list[str] | None = None) -> int:
    extra = list(sys.argv[1:] if argv is None else argv)
    found = current()
    try:
        command = found.build_command(*extra)
    except ValueError as exc:
        print(f"image_provenance: {exc}", file=sys.stderr)
        return 2
    state = (
        "DIRTY: uncommitted or untracked changes in the build context" if found.dirty else "clean"
    )
    print(f"# {found.revision} ({state})", file=sys.stderr)
    print(f"# run it in {ROOT} - the build context is '{CONTEXT}'", file=sys.stderr)
    print(shlex.join(command))
    return 0


if __name__ == "__main__":
    sys.exit(main())
