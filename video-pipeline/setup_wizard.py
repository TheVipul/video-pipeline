"""
First-run setup.

Asks the two questions that actually change how the pipeline behaves, writes
the answers to .env, and gets out of the way.

The important one is brand mode. Two features - watermarking and the
brand-relevance gate - only make sense when the pipeline is publishing on
behalf of a specific brand. Running them in general mode produces confusing
results: a watermark for a brand that does not exist, and videos held for
review for not matching a brand nobody configured. So they are switched off
together rather than left as separate flags a user has to reason about.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
BRANDS_DIR = ROOT / "configs" / "brands"


def _ask(prompt: str, options: list[tuple[str, str]], default: int = 0) -> str:
    """Present a numbered menu, return the chosen key."""
    print(f"\n{prompt}")
    for i, (_, label) in enumerate(options, 1):
        marker = "  (default)" if i - 1 == default else ""
        print(f"  {i}. {label}{marker}")
    while True:
        raw = input(f"\nChoice [1-{len(options)}]: ").strip()
        if not raw:
            return options[default][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("  Please enter a number from the list.")


def _set_env(lines: list[str], key: str, value: str) -> list[str]:
    """Set or append a key in .env, preserving comments and order."""
    out, found = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    return out


def available_brands() -> list[str]:
    return sorted(p.stem for p in BRANDS_DIR.glob("*.yaml") if p.stem != "generic")


def main() -> int:
    print("=" * 66)
    print("  Video Pipeline - first-run setup")
    print("=" * 66)

    if not ENV_PATH.exists():
        if ENV_EXAMPLE.exists():
            shutil.copy(ENV_EXAMPLE, ENV_PATH)
            print(f"\nCreated {ENV_PATH.name} from the example.")
        else:
            ENV_PATH.write_text("")

    lines = ENV_PATH.read_text().splitlines()

    # --- 1. brand mode
    mode = _ask(
        "How will you use this pipeline?",
        [
            ("general", "General use - process any video, no brand rules"),
            ("brand", "For a specific brand - adds watermark + brand-fit filtering"),
        ],
    )

    if mode == "brand":
        brands = available_brands()
        if brands:
            choice = _ask(
                "Which brand profile?",
                [(b, b) for b in brands] + [("__new__", "Create a new one")],
            )
        else:
            choice = "__new__"

        if choice == "__new__":
            slug = input("\n  Short name (e.g. acme_outdoors): ").strip().lower()
            slug = "".join(c if c.isalnum() or c == "_" else "_" for c in slug)
            if not slug:
                print("  No name given - falling back to general mode.")
                mode, brand = "general", "generic"
            else:
                template = BRANDS_DIR / "surlatable.yaml"
                target = BRANDS_DIR / f"{slug}.yaml"
                if not target.exists() and template.exists():
                    shutil.copy(template, target)
                print(f"\n  Created configs/brands/{slug}.yaml")
                print("  Edit it to set the tone, watermark text and prompts.")
                brand = slug
        else:
            brand = choice
    else:
        brand = "generic"

    # --- 2. where finished videos go
    publisher = _ask(
        "Where should finished videos be published?",
        [
            ("local", "A local folder (no setup needed)"),
            ("gdrive", "Google Drive (needs Google credentials)"),
            ("s3", "S3-compatible storage (needs keys)"),
        ],
    )

    lines = _set_env(lines, "PIPELINE_BRAND", brand)
    lines = _set_env(lines, "PIPELINE_MODE", mode)
    lines = _set_env(lines, "PIPELINE_PUBLISHER", publisher)
    # Both features are meaningless without a brand, so they move together.
    lines = _set_env(lines, "PIPELINE_ENABLE_WATERMARK", "true" if mode == "brand" else "false")
    lines = _set_env(lines, "PIPELINE_ENABLE_RELEVANCE_GATE", "true" if mode == "brand" else "false")
    ENV_PATH.write_text("\n".join(lines) + "\n")

    print("\n" + "=" * 66)
    print(f"  Mode          : {mode}")
    print(f"  Brand profile : {brand}")
    print(f"  Publish to    : {publisher}")
    print(f"  Watermark     : {'on' if mode == 'brand' else 'off (general mode)'}")
    print(f"  Brand filter  : {'on' if mode == 'brand' else 'off (general mode)'}")
    print("=" * 66)

    if publisher == "gdrive":
        print("\n  Google Drive needs one-time credentials:")
        print("    inputs/client_secret.json")
        print("  Run `python run.py --check-auth` to complete consent.")
    if publisher == "s3":
        print("\n  Set S3_ACCESS_KEY / S3_SECRET_KEY and S3_BUCKET in .env")

    print("\n  Add video URLs to inputs/urls.txt, then run:")
    print(f"    .venv/bin/python run.py --max 5 --publisher {publisher} --brand {brand}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
