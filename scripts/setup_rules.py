"""CLI: baixa o arquivo oficial de Regras Abrangentes (Comprehensive Rules).

Uso:
    python scripts/setup_rules.py
"""

import sys
from pathlib import Path

# Garante import de `services` ao rodar como script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.config import RULES_FILE
from services.rules_setup import RulesDownloadError, ensure_rules_file


def main() -> int:
    try:
        print("Garantindo Comprehensive Rules em data/MagicCompRules.txt ...")
        path = ensure_rules_file(RULES_FILE)
        size_kb = path.stat().st_size / 1024
        print(f"OK: regras em {path} ({size_kb:.0f} KB)")
        return 0
    except RulesDownloadError as exc:
        print(f"ERRO: {exc}")
        print(
            "Baixe manualmente o .txt em https://magic.wizards.com/en/rules "
            f"e salve como {RULES_FILE}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
