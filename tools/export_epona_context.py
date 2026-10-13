"""Exporta el conocimiento de un proyecto de aiviber (vía epona) a knowledge/<proyecto>.md.

    .venv/bin/python tools/export_epona_context.py                      # proyecto transport
    .venv/bin/python tools/export_epona_context.py --exclude ""         # incluir también notas técnicas
    .venv/bin/python tools/export_epona_context.py --skill transport-c24-warehouse

Usa scripts/memory.py de epona, que lee el token de $AIVIBER_TOKEN o de su .mcp.json (nunca lo
imprime). aiviber no tiene "listar todo", así que se hacen búsquedas por temas y se juntan los IDs.
knowledge/ está en .gitignore: es información privada del cliente.
"""
import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOPICS = """contabilidad factura asiento diario pago cliente proveedor nota credito NCF impuesto ITBIS banco
conciliacion cancelar anular borrar reporte balance cuenta catalogo analitica gasto compra venta inventario
almacen nomina empleado asistente usuario permiso modulo odoo cierre periodo fiscal DGII 606 607 cheque caja
moneda dolar retencion activo depreciacion presupuesto envio courier paquete guia rastreo portal precio tarifa
orden cobro vencimiento conductor ruta vehiculo""".split()

RATE_PAUSE = 1.2  # aiviber permite 60 llamadas/minuto


def with_retry(fn, *args):
    for _ in range(4):
        try:
            return fn(*args)
        except RuntimeError as exc:
            if "429" not in str(exc):
                raise
            time.sleep(30)
    raise RuntimeError("aiviber sigue limitando las llamadas (429)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--epona", default=str(Path.home() / "Documents/epona"))
    parser.add_argument("--project", default="transport")
    parser.add_argument("--exclude", default="infra,incident,deploy,portal,procedure",
                        help="prefijos de clave a omitir (notas técnicas que no sirven en una reunión con el cliente)")
    parser.add_argument("--skill", action="append", default=[],
                        help="skill local de epona a incluir completa (SKILL.md + references/)")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(args.epona) / "scripts"))
    import memory  # noqa: E402  (scripts/memory.py de epona)

    excluded = tuple(p.strip() + "/" for p in args.exclude.split(",") if p.strip())
    keys_by_id: dict[str, str] = {}
    for i, topic in enumerate(TOPICS, 1):
        for _project, item_id, key, *_ in with_retry(memory.search, args.project, topic, 50):
            if not key.startswith(excluded):
                keys_by_id.setdefault(item_id, key)
        print(f"\r[export] búsquedas {i}/{len(TOPICS)} · {len(keys_by_id)} notas", end="", flush=True)
        time.sleep(RATE_PAUSE)
    print()

    sections = []
    ids = list(keys_by_id)
    for start in range(0, len(ids), 10):
        batch = ids[start:start + 10]
        text = with_retry(memory.call, "memory_get", {"ids": ",".join(batch)}, args.project)
        sections.append(text.strip())
        time.sleep(RATE_PAUSE)

    out = [f"# Conocimiento del proyecto {args.project} (exportado de aiviber)\n", *sections]
    for skill in args.skill:
        skill_dir = Path(args.epona) / "skills" / skill
        for f in [skill_dir / "SKILL.md", *sorted((skill_dir / "references").glob("*.md"))]:
            if f.exists():
                body = re.sub(r"^---\n.*?\n---\n", "", f.read_text(encoding="utf-8"), flags=re.S)
                out.append(f"\n## Skill {skill}: {f.name}\n\n{body.strip()}")

    dest = ROOT / "knowledge" / f"{args.project}.md"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text("\n\n".join(out) + "\n", encoding="utf-8")
    print(f"[export] {len(keys_by_id)} notas → {dest.relative_to(ROOT)} ({dest.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
