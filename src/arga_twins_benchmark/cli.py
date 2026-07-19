from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from arga_twins_benchmark.catalog import fingerprint_instance_bundle, validate_catalog

app = typer.Typer(no_args_is_help=True, help="Arga Twins Benchmark tools")
catalog_app = typer.Typer(no_args_is_help=True, help="Validate and inspect benchmark catalog files")
app.add_typer(catalog_app, name="catalog")


@catalog_app.command("validate")
def catalog_validate(
    path: Annotated[Path, typer.Argument(help="Catalog file or directory")] = Path("benchmark"),
) -> None:
    documents = validate_catalog(path)
    for document in documents:
        typer.echo(f"{document.fingerprint[:12]}  {document.path}")
    typer.echo(f"Validated {len(documents)} catalog documents")


@catalog_app.command("fingerprint")
def catalog_fingerprint(
    instance_id: Annotated[str, typer.Argument(help="Instance identifier")],
    root: Annotated[Path, typer.Option(help="Catalog root")] = Path("benchmark"),
) -> None:
    typer.echo(fingerprint_instance_bundle(root, instance_id))


if __name__ == "__main__":
    app()
